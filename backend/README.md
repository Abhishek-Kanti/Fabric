# Company Brain — Backend

Backend service for Company Brain, an organizational memory and intelligence platform.

## Development Setup

### Prerequisites
- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) (recommended)

### Installation
```bash
# Create virtual environment and install dependencies
uv venv
uv pip install -e ".[dev]"
```

### Running the API Server
```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Running Tests
```bash
uv run pytest
```

## Docker

### Build and Run with Docker
```bash
# From the backend directory:
docker build -t company-brain-backend .
docker run -p 8000:8000 company-brain-backend
```

### Build and Run with Docker Compose
```bash
# From the repository root:
docker compose up --build
```

Health check endpoint: `http://localhost:8000/api/v1/health`
OpenAPI documentation: `http://localhost:8000/docs`
