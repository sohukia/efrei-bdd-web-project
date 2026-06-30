# Projet de Base de Donnée et Web

## Setup

> Ensure `docker` and `docker compose` are installed.
> Ensure [uv](https://docs.astral.sh/uv/getting-started/installation/) is installed.

1. Clone the repository
```bash
git clone https://github.com/sohukia/efrei-bdd-web-project.git && cd efrei-bdd-web-project
```
2. Create a virtual environment
```bash
uv venv
````
Activate the virtual environment with the command shown after.

3. Install dependencies
```bash
uv sync
```

4. Run the local database
```bash
docker compose up -d
```
