# Ontology Explorer V6 — Cloud

A Streamlit application for uploading and visualizing OWL/RDF ontologies in Turtle (`.ttl`) format.

## Privacy-oriented repository design

This repository intentionally contains **no default ontology file**. The application starts as a blank project and asks each user to upload a `.ttl` file from their own computer.

Uploaded ontologies are processed in a temporary Streamlit session workspace. They are not committed to this repository by the application.

## Run locally

```bash
python -m venv .venv
```

Activate the environment and install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
streamlit run ontology_explorer_v6_cloud.py
```

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. Open Streamlit Community Cloud.
3. Create a new app from the GitHub repository.
4. Select `ontology_explorer_v6_cloud.py` as the entrypoint.
5. Select the same Python major/minor version you tested locally.
6. Deploy.

## Important session behavior

The cloud application uses temporary session storage for builder overlays and proposal history. Users should download generated files before ending or resetting a session.

## Repository contents

```text
ontology-explorer/
├── ontology_explorer_v6_cloud.py
├── requirements.txt
├── README.md
└── .gitignore
```

Do not commit institutional or restricted ontology files to the public repository.
