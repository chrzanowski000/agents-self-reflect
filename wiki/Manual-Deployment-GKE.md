# Manual: Deployment — GKE (Google Kubernetes Engine)

[← Home](Home.md) | [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) | [Configuration](Manual-Configuration-and-Secrets.md) | [Operations](Manual-Operations-and-Troubleshooting.md)

This guide walks through deploying the platform to Google Kubernetes Engine using Google Artifact Registry for container images. Two deployment paths are supported — choose one:

| Path | Tool | Script | Namespace |
|------|------|--------|-----------|
| **Kustomize** | `kubectl apply -k` | `scripts/deploy-gke.sh` | `default` |
| **Helm** | `helm upgrade --install` | `scripts/deploy-helm-gke.sh` | `agents` |

Both paths share the same GCP setup, IAM, build, and secrets steps (Parts 1–4). Only the deploy step differs.

> **Apple Silicon (M1/M2/M3) users:** Docker images must be built for `linux/amd64` to run on GKE nodes. The build script handles this automatically — no manual action needed.

---

## Prerequisites

Install the following tools before starting:

| Tool | Purpose | Install |
|------|---------|---------|
| `gcloud` CLI | GCP auth, cluster management, Artifact Registry | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| `docker` | Build and push container images | [docs.docker.com](https://docs.docker.com/get-docker/) |
| `kubectl` | Deploy and manage Kubernetes resources | `gcloud components install kubectl` |
| `helm` | Helm deployment path + nginx ingress controller | [helm.sh/docs/intro/install](https://helm.sh/docs/intro/install/) |
| `op` (1Password CLI) | Inject secrets from 1Password into the cluster | [developer.1password.com/docs/cli](https://developer.1password.com/docs/cli/get-started/) |

You also need:
- A **GCP project** with billing enabled
- Your `.env_tpl` file populated with `op://` secret references (see [Configuration and Secrets](Manual-Configuration-and-Secrets.md))

---

## Part 1 — GCP Setup

### Step 1 — Authenticate

```bash
gcloud auth login
gcloud auth application-default login
```

- `auth login` — authenticates your shell session so `gcloud` commands work
- `application-default login` — stores credentials that SDKs and tools (like Terraform) use to call GCP APIs on your behalf

### Step 2 — Set your project

```bash
gcloud config set project YOUR_PROJECT_ID
```

Verify:
```bash
gcloud config get project
```

To find your project ID:
```bash
gcloud projects list
```

Or in the GCP Console: click the project dropdown at the top — the ID is shown in the **ID** column (not the display name).

### Step 3 — Enable required APIs

```bash
gcloud services enable container.googleapis.com artifactregistry.googleapis.com
```

This takes ~30 seconds and is only needed once per project. `container.googleapis.com` enables GKE; `artifactregistry.googleapis.com` enables the container image registry.

### Step 4 — Create a GKE cluster

> **You can skip this step.** The deploy scripts (`deploy-gke.sh` / `deploy-helm-gke.sh`) automatically create the cluster if it doesn't exist when `GKE_CLUSTER` is set. Use `AUTOPILOT=1` to create an Autopilot cluster, or omit it for Standard. See Part 5 for details.

If you prefer to create the cluster manually, choose one mode:

**Autopilot (recommended)**

Fully managed — GKE provisions nodes only when pods are scheduled and releases them when idle. You pay only for pod CPU and memory while running. No node boot disks count against your Persistent Disk SSD quota.

```bash
gcloud container clusters create-auto agents-cluster \
  --region europe-west1
```

**Standard cluster**

Fixed nodes that run 24/7. Each node has a boot disk (default ~100 GB SSD) that counts against your Persistent Disk SSD quota even when no pods are scheduled.

```bash
gcloud container clusters create agents-cluster \
  --region europe-west1 \
  --num-nodes 1 \
  --machine-type e2-standard-2 \
  --disk-size 50
```

> **Cost warning:** A Standard cluster with a single `e2-standard-2` node and 50 GB disk costs roughly $50–60/month. The 50 GB disk replaces the 100 GB default — reducing the disk size is important if you're near your Persistent Disk SSD quota (default quota is 500 GB per region; each node's boot disk counts toward it).

Replace `europe-west1` with your preferred region. Cluster creation takes 3–5 minutes.

### Step 5 — Get cluster credentials

```bash
gcloud container clusters get-credentials agents-cluster \
  --region europe-west1 \
  --project YOUR_PROJECT_ID
```

This writes a `kubeconfig` entry so `kubectl` can communicate with the cluster.

Verify:
```bash
kubectl get nodes
```

You should see one or more nodes in `Ready` state (Autopilot may show no nodes until pods are scheduled — that is normal).

---

## Part 2 — Grant Artifact Registry Access

### Step 6 — Allow GKE nodes to pull images

GKE nodes pull container images using the **Compute Engine default service account**. By default this account does not have read access to Artifact Registry, which causes `ImagePullBackOff` errors. Grant the permission once per project:

```bash
PROJECT=YOUR_PROJECT_ID

SA=$(gcloud iam service-accounts list \
  --filter="displayName:Compute Engine default service account" \
  --format="value(email)" \
  --project="${PROJECT}")

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role="roles/artifactregistry.reader"
```

This is a one-time setup per GCP project. All GKE clusters in the project share the same default service account.

**Via GCP Console:**
1. Go to **IAM & Admin** → **IAM**
2. Click **Grant Access**
3. In **New principals**, paste the service account email (find it at **IAM & Admin** → **Service Accounts** — look for "Compute Engine default service account")
4. In **Role**, search for and select **Artifact Registry Reader**
5. Click **Save**

---

## Part 3 — Build and Push Images

### Step 7 — Build and push to Artifact Registry

```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
IMAGE_TAG=v1.0 \
./scripts/build-and-push-gke.sh
```

The script:
1. Runs `gcloud auth configure-docker` to authenticate Docker with Artifact Registry
2. Creates the `agents` Artifact Registry repository if it doesn't exist
3. Builds all three service images with `--platform linux/amd64` (required for GKE x86 nodes — handles Apple Silicon automatically via QEMU emulation)
4. Tags and pushes all images to Artifact Registry

> **Note:** Builds with `--platform linux/amd64` on Apple Silicon are slower due to cross-compilation via QEMU. This is expected.

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT` | Yes | — | Your GCP project ID |
| `GCP_REGION` | Yes | — | Region (e.g. `europe-west1`) |
| `IMAGE_TAG` | No | `latest` | Tag applied to all three images |

> **Tip:** Always use versioned tags (`v1.0`, `v1.1`) rather than `latest`. Versioned tags make it easy to roll back and to diagnose which image version is running.

**Resulting image URIs:**
```
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/chat-ui:v1.0
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/langgraph-api:v1.0
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/persistence-api:v1.0
```

To list pushed images:
```bash
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents
```

---

## Part 4 — Inject Secrets

### Step 8 — Create the `app-secrets` Kubernetes Secret

Secrets are **not baked into Docker images**. They live in the Kubernetes cluster as a Secret object named `app-secrets`. When pods start, Kubernetes injects the secret values as environment variables into the running containers.

The platform requires four secret values:

| Key | Description |
|-----|-------------|
| `OPENROUTER_API_KEY` | LLM API key (OpenRouter) |
| `LANGSMITH_API_KEY` | LangSmith tracing API key |
| `TAVILY_API_KEY` | Tavily web search API key |
| `POSTGRES_PASSWORD` | PostgreSQL database password |

> **Namespace is critical.** The secret must exist in the same namespace as the pods:
> - **Helm** deploys to the `agents` namespace → secret must be in `agents`
> - **Kustomize** deploys to the `default` namespace → secret must be in `default`

**Automated — Helm (1Password CLI):**

```bash
GKE_CLUSTER=agents-cluster \
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
./scripts/inject-secrets-gke.sh
```

This creates `app-secrets` in the `agents` namespace. The script fetches cluster credentials, confirms the kubectl context, then reads `.env_tpl` and resolves all `op://` references via the 1Password CLI.

**Automated — Kustomize (1Password CLI):**

```bash
GKE_CLUSTER=agents-cluster \
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
K8S_NAMESPACE=default \
./scripts/inject-secrets-gke.sh
```

**Manual fallback (without 1Password) — Helm:**

```bash
kubectl create secret generic app-secrets \
  --namespace agents \
  --from-literal=OPENROUTER_API_KEY=sk-... \
  --from-literal=LANGSMITH_API_KEY=ls-... \
  --from-literal=TAVILY_API_KEY=tvly-... \
  --from-literal=POSTGRES_PASSWORD=yourpassword \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Manual fallback (without 1Password) — Kustomize:**

```bash
kubectl create secret generic app-secrets \
  --namespace default \
  --from-literal=OPENROUTER_API_KEY=sk-... \
  --from-literal=LANGSMITH_API_KEY=ls-... \
  --from-literal=TAVILY_API_KEY=tvly-... \
  --from-literal=POSTGRES_PASSWORD=yourpassword \
  --dry-run=client -o yaml | kubectl apply -f -
```

Verify the secret was created in the correct namespace:
```bash
kubectl get secret app-secrets -n agents   # Helm
kubectl get secret app-secrets -n default  # Kustomize
```

> **Note:** The 1Password-based approach is the current supported method and is considered temporary. Future alternatives using GCP Secret Manager with External Secrets Operator or Workload Identity Federation will be documented separately once implemented. These approaches avoid storing secrets in local shell sessions entirely.

---

## Part 5a — Deploy with Kustomize

Kustomize deploys all resources into the **`default` namespace.

### Step 9 — Install the nginx ingress controller

The nginx ingress controller provides a cloud load balancer with a public external IP that routes HTTP traffic into the cluster.

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace
```

Wait for the load balancer external IP to be assigned. On GKE Autopilot this takes 2–5 minutes:

```bash
kubectl get svc -n ingress-nginx --watch
# Wait until EXTERNAL-IP changes from <pending> to a real IP:
# ingress-nginx-controller   LoadBalancer   10.x.x.x   34.90.x.x   80:31234/TCP
```

Press `Ctrl+C` once the IP appears.

### Step 10 — Deploy

**With an existing cluster:**
```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
./scripts/deploy-gke.sh
```

**Let the script create an Autopilot cluster automatically (recommended):**
```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
AUTOPILOT=1 \
./scripts/deploy-gke.sh
```

**Let the script create a Standard cluster automatically:**
```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
./scripts/deploy-gke.sh
```

What the script does:
1. Creates the cluster if it doesn't exist:
   - `AUTOPILOT=1` → Autopilot cluster (scales to zero, no idle disk costs)
   - No flag → Standard cluster (1 node, `e2-standard-2`, 50 GB disk)
2. Fetches cluster credentials (`gcloud container clusters get-credentials`)
3. Copies the `infrastructure/k8s/` directory to a temp directory
4. Runs `envsubst` to substitute `${GCP_PROJECT}`, `${GCP_REGION}`, `${IMAGE_TAG}` into `infrastructure/k8s/gke/kustomization.yaml`
5. Runs `kubectl apply -k` on the rendered Kustomize overlay
6. Waits for all deployments and the postgres StatefulSet to finish rolling out

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT` | Yes | — | GCP project ID |
| `GCP_REGION` | Yes | — | Region (e.g. `europe-west1`) |
| `GKE_CLUSTER` | No | — | Cluster name; fetches credentials if set |
| `IMAGE_TAG` | No | `latest` | Image tag to deploy |
| `AUTOPILOT` | No | `0` | Set to `1` to create an Autopilot cluster |

### Step 11 — Access the app

```bash
kubectl get svc -n ingress-nginx
# Copy the EXTERNAL-IP value
```

Open `http://EXTERNAL-IP` in your browser.

---

## Part 5b — Deploy with Helm

Helm deploys all resources into the **`agents` namespace**.

### Step 9 — Install the nginx ingress controller

Same as Kustomize Step 9 above.

### Step 10 — (Optional) Set a hostname

By default, `values-gke.yaml` sets `ingress.host: ""` which creates a catch-all ingress rule — the app is accessible via the load balancer's external IP without needing a domain.

If you have a domain, edit `infrastructure/helm/research-agent-platform/values-gke.yaml`:

```yaml
ingress:
  host: agent.yourdomain.com
```

Leave `host: ""` if you don't have a domain — the app will work via IP.

### Step 11 — Deploy

**With an existing cluster:**
```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
./scripts/deploy-helm-gke.sh
```

**Let the script create an Autopilot cluster automatically (recommended):**
```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
AUTOPILOT=1 \
./scripts/deploy-helm-gke.sh
```

What the script does:
1. Creates the cluster if it doesn't exist (`AUTOPILOT=1` → Autopilot, default → Standard)
2. Fetches cluster credentials
3. Runs `envsubst` on `values-gke.yaml` to substitute image URIs with real values
4. Runs `helm upgrade --install agents` with both `values.yaml` (base) and the rendered GKE values
5. Waits for all deployments and the postgres StatefulSet to finish rolling out

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT` | Yes | — | GCP project ID |
| `GCP_REGION` | Yes | — | Region (e.g. `europe-west1`) |
| `GKE_CLUSTER` | No | — | Cluster name; fetches credentials if set |
| `IMAGE_TAG` | No | `latest` | Image tag to deploy |
| `AUTOPILOT` | No | `0` | Set to `1` to create an Autopilot cluster |
| `HELM_RELEASE` | No | `agents` | Helm release name |
| `HELM_NAMESPACE` | No | `agents` | Kubernetes namespace |

### Step 12 — Access the app

```bash
kubectl get svc -n ingress-nginx
# Copy the EXTERNAL-IP value
```

Open `http://EXTERNAL-IP` (or your configured hostname) in your browser.

---

## Verify Deployment

**Kustomize (`default` namespace):**
```bash
kubectl get pods
kubectl get pods -n ingress-nginx

# LangGraph API health
kubectl port-forward svc/langgraph-api 2024:2024 &
curl http://localhost:2024/ok

# Persistence API health
kubectl port-forward svc/persistence-api 8001:8001 &
curl http://localhost:8001/health
```

**Helm (`agents` namespace):**
```bash
kubectl get pods -n agents
kubectl get pods -n ingress-nginx

# LangGraph API health
kubectl port-forward svc/langgraph-api 2024:2024 -n agents &
curl http://localhost:2024/ok

# Persistence API health
kubectl port-forward svc/persistence-api 8001:8001 -n agents &
curl http://localhost:8001/health
```

**Inspect a failing pod:**
```bash
kubectl describe pod <pod-name> -n <namespace>
kubectl logs <pod-name> -n <namespace>
```

---

## Troubleshooting

### `CrashLoopBackOff` — exec format error (Apple Silicon)

**Cause:** Images were built on an Apple Silicon Mac (ARM64) but GKE nodes run x86_64. Pod logs show:
```
exec /usr/local/bin/docker-entrypoint.sh: exec format error
```

**Fix:** The build script already includes `--platform linux/amd64`. Rebuild with a new tag and redeploy:

```bash
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 IMAGE_TAG=v1.1 \
  ./scripts/build-and-push-gke.sh

# Kustomize:
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 GKE_CLUSTER=agents-cluster IMAGE_TAG=v1.1 \
  ./scripts/deploy-gke.sh

# Helm:
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 GKE_CLUSTER=agents-cluster IMAGE_TAG=v1.1 \
  ./scripts/deploy-helm-gke.sh
```

The build will be slower (QEMU cross-compilation) but the images will run correctly on GKE.

### `ImagePullBackOff`

**Cause:** GKE nodes cannot pull images from Artifact Registry — missing IAM permission.

**Diagnose:**
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "failed to pull image ... permission denied"
```

**Fix:** Grant `roles/artifactregistry.reader` to the Compute Engine default service account (see Part 2 — Step 6). After granting, pods will retry automatically within a few minutes.

### `CreateContainerConfigError` — app-secrets not found

**Cause:** The `app-secrets` Kubernetes Secret doesn't exist, or it exists in the wrong namespace.

**Diagnose:**
```bash
kubectl get secret app-secrets -n agents   # Helm
kubectl get secret app-secrets -n default  # Kustomize
```

**Fix:** Re-inject secrets into the correct namespace:

```bash
# Helm (agents namespace — default for inject-secrets-gke.sh):
GKE_CLUSTER=agents-cluster GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 \
  ./scripts/inject-secrets-gke.sh

# Kustomize (default namespace):
GKE_CLUSTER=agents-cluster GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 \
  K8S_NAMESPACE=default ./scripts/inject-secrets-gke.sh
```

Then restart the affected deployments:
```bash
kubectl rollout restart deployment/langgraph-api deployment/persistence-api -n <namespace>
```

### No EXTERNAL-IP for ingress controller

**Cause:** nginx ingress controller not installed, not yet provisioned, or GKE firewall blocking LoadBalancer.

**Diagnose:**
```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller
# If EXTERNAL-IP is <pending>, check events:
kubectl describe svc -n ingress-nginx ingress-nginx-controller
```

On Autopilot clusters, LoadBalancer provisioning takes 3–5 minutes. Wait and re-check with `--watch`.

### PVC stuck in `Pending`

**Cause:** Storage class not available or wrong name.

**Diagnose:**
```bash
kubectl get storageclass
# Should show: standard-rwo (default on GKE)
kubectl describe pvc langgraph-data
kubectl describe pvc postgres-data-postgres-0
```

If the storage class name differs, update `storageClassName` in `infrastructure/k8s/gke/langgraph-data-pvc.yaml` (Kustomize) or `values-gke.yaml` (Helm).

### Ingress returns 404

**Cause:** nginx ingress controller not registered the Ingress resource, or `ingressClassName: nginx` mismatch.

**Diagnose:**
```bash
kubectl get ingressclass
# Should show: nginx

kubectl describe ingress agents-ingress -n <namespace>
```

### Chat UI calls wrong API URL (CORS errors / "agent.local")

**Symptom:** The browser console shows CORS errors or failed requests to `http://agent.local/api/...` even though your cluster is running at a different IP.

**Cause:** `NEXT_PUBLIC_*` variables in Next.js are **baked into the JavaScript bundle at build time** — they are not read from pod environment variables at runtime. The Dockerfile defaults `NEXT_PUBLIC_API_URL=http://agent.local/api`. If the image was built without overriding this ARG, that hardcoded value is in the JS bundle regardless of what you set in `values-gke.yaml` or Kubernetes env vars.

**Fix:** The `build-and-push-gke.sh` script now defaults to `/api` (a relative URL that works from any domain or IP):

```bash
CHAT_UI_API_URL="${NEXT_PUBLIC_API_URL:-/api}"
docker build --build-arg NEXT_PUBLIC_API_URL="${CHAT_UI_API_URL}" ...
```

Rebuild with a new tag and redeploy:

```bash
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 IMAGE_TAG=v1.2 \
  ./scripts/build-and-push-gke.sh

# Helm:
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 \
  GKE_CLUSTER=agents-cluster IMAGE_TAG=v1.2 AUTOPILOT=1 \
  ./scripts/deploy-helm-gke.sh

# Kustomize:
GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=europe-west1 \
  GKE_CLUSTER=agents-cluster IMAGE_TAG=v1.2 \
  ./scripts/deploy-gke.sh
```

After redeployment, clear any stale `?apiUrl=` query parameter from your browser URL — old tabs may have it cached.

> **Note for local Docker Desktop:** `build-images.sh` (local build script) still uses `http://agent.local/api` as default, which is correct for local development. Only GKE builds use `/api`.

---

## Tear Down

```bash
# Remove deployed resources — Kustomize:
kubectl delete -k infrastructure/k8s/gke

# Remove deployed resources — Helm:
helm uninstall agents -n agents

# Delete secrets:
kubectl delete secret app-secrets -n agents   # Helm
kubectl delete secret app-secrets -n default  # Kustomize

# Delete the GKE cluster (stops billing for nodes and their boot disks):
gcloud container clusters delete agents-cluster \
  --region europe-west1 \
  --project YOUR_PROJECT_ID

# Delete the Artifact Registry repository (stops billing for image storage):
gcloud artifacts repositories delete agents \
  --location europe-west1 \
  --project YOUR_PROJECT_ID
```

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) — Local dev, EKS, and generic Helm
- [Configuration and Secrets](Manual-Configuration-and-Secrets.md) — All env vars and secret management
- [Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) — Day-2 ops, logs, restarts
