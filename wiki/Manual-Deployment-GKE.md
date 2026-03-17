# Manual: Deployment — GKE (Google Kubernetes Engine)

[← Home](Home.md) | [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) | [Configuration](Manual-Configuration-and-Secrets.md) | [Operations](Manual-Operations-and-Troubleshooting.md)

This guide walks through deploying the platform to Google Kubernetes Engine using Google Artifact Registry for container images. Two deployment paths are supported — choose one:

| Path | Tool | Script |
|------|------|--------|
| **Kustomize** | `kubectl apply -k` | `scripts/deploy-gke.sh` |
| **Helm** | `helm upgrade --install` | `scripts/deploy-helm-gke.sh` |

Both paths use the same build, secrets, and cluster setup steps.

---

## Prerequisites

Install the following tools before starting:

| Tool | Purpose | Install |
|------|---------|---------|
| `gcloud` CLI | GCP auth, cluster, Artifact Registry | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| `docker` | Build and push images | [docs.docker.com](https://docs.docker.com/get-docker/) |
| `kubectl` | Deploy to Kubernetes | `gcloud components install kubectl` |
| `helm` | Helm deployment path only | [helm.sh/docs/intro/install](https://helm.sh/docs/intro/install/) |
| `op` (1Password CLI) | Inject secrets | [developer.1password.com/docs/cli](https://developer.1password.com/docs/cli/get-started/) |

You also need:
- A **GCP project** with billing enabled
- An existing GKE cluster, or permission to create one
- Your `.env_tpl` file populated with `op://` secret references (see [Configuration and Secrets](Manual-Configuration-and-Secrets.md))

---

## Part 1 — GCP Setup

### Step 1 — Authenticate

```bash
gcloud auth login
gcloud auth application-default login
```

`auth login` authenticates your shell. `application-default login` is needed for tools (like Terraform or SDKs) that call GCP APIs on your behalf.

### Step 2 — Set your project

```bash
gcloud config set project YOUR_PROJECT_ID
```

Verify:
```bash
gcloud config get project
```

### Step 3 — Enable required APIs

```bash
gcloud services enable container.googleapis.com artifactregistry.googleapis.com
```

This takes ~30 seconds. Only needed once per project.

### Step 4 — Create a GKE cluster

**Autopilot (recommended)** — fully managed, no node configuration needed:

```bash
gcloud container clusters create-auto agents-cluster \
  --region europe-west1
```

**Standard cluster** — if you need manual node control:

```bash
gcloud container clusters create agents-cluster \
  --region europe-west1 \
  --num-nodes 2 \
  --machine-type e2-standard-2
```

Replace `europe-west1` with your preferred region. Creating the cluster takes 3–5 minutes.

### Step 5 — Get cluster credentials

```bash
gcloud container clusters get-credentials agents-cluster \
  --region europe-west1 \
  --project YOUR_PROJECT_ID
```

Verify:
```bash
kubectl get nodes
```

---

## Part 2 — Build and Push Images to Artifact Registry

### Step 6 — Run the build script

```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
IMAGE_TAG=v1.0 \
./scripts/build-and-push-gke.sh
```

The script:
1. Configures Docker to authenticate with Artifact Registry (`gcloud auth configure-docker`)
2. Creates the `agents` Artifact Registry repository if it doesn't exist
3. Builds the three service images from their Dockerfiles
4. Tags and pushes all three images

**Environment variables:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GCP_PROJECT` | Yes | — | Your GCP project ID |
| `GCP_REGION` | Yes | — | Region (e.g. `europe-west1`) |
| `IMAGE_TAG` | No | `latest` | Tag to apply to all images |

**Resulting image URIs:**
```
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/chat-ui:v1.0
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/langgraph-api:v1.0
europe-west1-docker.pkg.dev/YOUR_PROJECT_ID/agents/persistence-api:v1.0
```

---

## Part 3 — Inject Secrets

### Step 7 — Inject API keys into the cluster

The platform requires four secrets: `OPENROUTER_API_KEY`, `LANGSMITH_API_KEY`, `TAVILY_API_KEY`, and `POSTGRES_PASSWORD`. These are stored in a Kubernetes secret named `app-secrets`.

**Automated (1Password CLI):**

```bash
GKE_CLUSTER=agents-cluster \
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
./scripts/inject-secrets-gke.sh
```

The script fetches cluster credentials (if `GKE_CLUSTER` is set), prints the current kubectl context for confirmation, then runs `inject-secrets.sh` which reads `.env_tpl` and resolves all `op://` references via the 1Password CLI.

**Manual fallback (without 1Password):**

```bash
kubectl create secret generic app-secrets \
  --from-literal=OPENROUTER_API_KEY=sk-... \
  --from-literal=LANGSMITH_API_KEY=ls-... \
  --from-literal=TAVILY_API_KEY=tvly-... \
  --from-literal=POSTGRES_PASSWORD=yourpassword \
  --dry-run=client -o yaml | kubectl apply -f -
```

> **Note:** The 1Password-based approach is the current supported method and is considered temporary. Future alternatives using GCP Secret Manager with External Secrets Operator or Workload Identity Federation will be documented separately once implemented. These approaches avoid storing secrets in local shell sessions.

---

## Part 4a — Deploy with Kustomize

### Step 8 — Install the nginx ingress controller

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace
```

Wait for the load balancer IP to be assigned (takes ~2 minutes on GKE):

```bash
kubectl get svc -n ingress-nginx
# NAME                                 TYPE           CLUSTER-IP    EXTERNAL-IP
# ingress-nginx-controller             LoadBalancer   10.x.x.x      34.90.x.x   <-- wait for this
```

### Step 9 — Deploy

```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
./scripts/deploy-gke.sh
```

The script:
1. Fetches cluster credentials (if `GKE_CLUSTER` is set)
2. Copies the k8s directory to a temp dir and runs `envsubst` to substitute `${GCP_PROJECT}`, `${GCP_REGION}`, `${IMAGE_TAG}` in `infrastructure/k8s/gke/kustomization.yaml`
3. Runs `kubectl apply -k` on the rendered overlay
4. Waits for all deployment and statefulset rollouts

### Step 10 — Access the app

```bash
kubectl get svc -n ingress-nginx
# Copy the EXTERNAL-IP
```

Open `http://EXTERNAL-IP` in your browser.

---

## Part 4b — Deploy with Helm (alternative)

### Step 8 — Install the nginx ingress controller

Same as Kustomize Step 8 above.

### Step 9 — (Optional) Set a hostname

If you have a domain, edit `infrastructure/helm/research-agent-platform/values-gke.yaml` and set:

```yaml
ingress:
  host: agent.yourdomain.com
```

Leave `host: ""` to use the load balancer IP directly (no domain needed).

### Step 10 — Deploy

```bash
GCP_PROJECT=YOUR_PROJECT_ID \
GCP_REGION=europe-west1 \
GKE_CLUSTER=agents-cluster \
IMAGE_TAG=v1.0 \
./scripts/deploy-helm-gke.sh
```

The script:
1. Fetches cluster credentials (if `GKE_CLUSTER` is set)
2. Runs `envsubst` on `values-gke.yaml` to render image URIs
3. Runs `helm upgrade --install` with both `values.yaml` and the rendered GKE values
4. Waits for rollouts

### Step 11 — Access the app

```bash
kubectl get svc -n ingress-nginx
```

Open `http://EXTERNAL-IP` (or your configured hostname).

---

## Verify Deployment

```bash
# All pods running
kubectl get pods

# Check a failing pod
kubectl describe pod <pod-name>
kubectl logs <pod-name>

# LangGraph API health
kubectl port-forward svc/langgraph-api 2024:2024 &
curl http://localhost:2024/ok

# Persistence API health
kubectl port-forward svc/persistence-api 8001:8001 &
curl http://localhost:8001/health
```

---

## Troubleshooting

### `ImagePullBackOff`

**Cause:** Kubernetes cannot pull images from Artifact Registry.

**Fix:**
```bash
kubectl describe pod <pod-name>
# Look for: "failed to pull image ... permission denied"
```

Re-run the Docker auth step:
```bash
gcloud auth configure-docker europe-west1-docker.pkg.dev
```

On GKE, node service accounts need `roles/artifactregistry.reader`. Grant it:
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud iam service-accounts list --filter='displayName:Compute Engine default service account' --format='value(email)')" \
  --role="roles/artifactregistry.reader"
```

### `CrashLoopBackOff` on langgraph-api or persistence-api

**Cause:** Missing or incorrect secrets.

**Fix:**
```bash
kubectl logs deployment/langgraph-api
# Look for: "ConfigError: Missing required API key"

# Verify secret exists and has correct keys
kubectl get secret app-secrets -o yaml
```

Re-run `inject-secrets-gke.sh` if needed.

### No EXTERNAL-IP for ingress controller

**Cause:** nginx ingress controller not installed, or GKE firewall blocking LoadBalancer.

**Fix:**
```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller
# If EXTERNAL-IP is <pending> after 5 min, check events:
kubectl describe svc -n ingress-nginx ingress-nginx-controller
```

On Autopilot clusters, LoadBalancer provisioning can take 3–5 minutes. Wait and re-check.

### PVC stuck in `Pending`

**Cause:** Storage class not available or wrong name.

**Fix:**
```bash
kubectl get storageclass
# Should show: standard-rwo (default on GKE)
kubectl describe pvc langgraph-data
kubectl describe pvc postgres-data-postgres-0
```

If the storage class name differs, update `storageClassName` in `infrastructure/k8s/gke/langgraph-data-pvc.yaml` and `values-gke.yaml`.

### Ingress returns 404

**Cause:** nginx ingress controller not picked up the Ingress resource, or `ingressClassName: nginx` mismatch.

**Fix:**
```bash
kubectl get ingressclass
# Should show: nginx

kubectl describe ingress agents-ingress
```

---

## Tear Down

```bash
# Delete all deployed resources
kubectl delete -k infrastructure/k8s/gke
# OR for Helm:
helm uninstall agents -n agents

# Delete the GKE cluster (stops billing for nodes)
gcloud container clusters delete agents-cluster \
  --region europe-west1 \
  --project YOUR_PROJECT_ID

# Delete the Artifact Registry repository (stops billing for storage)
gcloud artifacts repositories delete agents \
  --location europe-west1 \
  --project YOUR_PROJECT_ID
```

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) — Local dev, EKS, and generic Helm
- [Configuration and Secrets](Manual-Configuration-and-Secrets.md) — All env vars and secret management
- [Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) — Day-2 ops, logs, restarts
