# Build CONTEXT must be the lvkit repo ROOT (the package source is installed).
#   from repo root:  gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml .
# or, locally:       docker build -f deploy/cloudrun/Dockerfile -t lvkit-render .
FROM python:3.12-slim

# Install lvkit (this branch, incl. bundled primitives/vilib JSON) + web deps.
# pylabview comes in as an lvkit dependency (pure Python — no LabVIEW needed).
COPY . /src
RUN pip install --no-cache-dir /src flask gunicorn

# Run from the service dir so `main:app` and ./static resolve.
WORKDIR /src/deploy/cloudrun
ENV PORT=8080
# One worker per ~0.5-1 CPU; threads let a slow extraction not block health checks.
CMD exec gunicorn -b :$PORT -w 2 -t 120 main:app
