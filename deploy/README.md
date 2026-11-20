# Deploying Agent B to GKE

Railway is still the production target (`railway.toml`). This directory runs the
same image on a small GKE Autopilot cluster instead:

- `terraform/` creates the VPC, an Autopilot cluster, an Artifact Registry repo, a
  global static IP for the Ingress, and a Memorystore Redis instance (1 GB, basic
  tier) for OAuth state (`REDIS_URL`).
- `k8s/` holds one Deployment (1 replica), a 1 Gi volume for the tool-usage JSONL,
  and a Service plus a GCE Ingress with a Google-managed TLS certificate.

The image is the repo's `Dockerfile`, unchanged. `start.sh` still brings up
Tailscale in userspace mode for the OPERA path, so the pod needs no extra
privileges, sidecar or `/dev/net/tun`.

## 1. Infrastructure

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id / region
terraform init
terraform apply
```

Point a DNS A record for your hostname at `terraform output ingress_ip`.

## 2. Image

```bash
REPO=$(terraform -chdir=deploy/terraform output -raw image_repository)
gcloud auth configure-docker "${REPO%%/*}"
docker build -t "$REPO:$(git rev-parse --short HEAD)" .
docker push "$REPO:$(git rev-parse --short HEAD)"
```

## 3. Configure the manifests

In `k8s/`:

- `kustomization.yaml`: set `newName` to the image repository and `newTag` to the
  tag you pushed.
- `managedcertificate.yaml` and `ingress.yaml`: replace `agent-b.example.com` with
  your hostname. It must match `OAUTH_PUBLIC_URL`, and the Google OAuth client
  needs `https://<host>/oauth/callback` as a redirect URI.

## 4. Secret and deploy

The container reads all of its config from one Secret, `agent-b-env`, built from
a production env file (same variables as `.env.example`):

```bash
gcloud container clusters get-credentials agent-b --region <region>
kubectl apply -f deploy/k8s/namespace.yaml

# prod.env = your Railway variables, plus:
#   REDIS_URL=$(terraform -chdir=deploy/terraform output -raw redis_url)
#   OAUTH_PUBLIC_URL=https://<host>
kubectl -n agent-b create secret generic agent-b-env --from-env-file=prod.env

kubectl apply -k deploy/k8s
```

Do not set `TOOL_USAGE_LOG` in the env file; the Deployment points it at the
mounted volume.

To change config later, recreate the Secret and restart the pod:

```bash
kubectl -n agent-b create secret generic agent-b-env --from-env-file=prod.env \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n agent-b rollout restart deployment/agent-b
```

The managed certificate takes 15–60 minutes to become `Active` after DNS
resolves. Check it with `kubectl -n agent-b describe managedcertificate agent-b`.

## Notes

- **One replica, `Recreate` strategy.** The usage-log volume is ReadWriteOnce, so
  each deploy has a few seconds of downtime, just like Railway.
- **OPERA tunnel.** Every pod start creates a fresh ephemeral Tailscale node
  (`agent-b-<pod-name>`), which needs 1–3 minutes to reach OPERA. This is the
  same as on Railway.
- **Usage report.** The weekly workflow calls `AGENT_B_URL` over HTTP, so if you
  cut over, point that secret at the new host.
