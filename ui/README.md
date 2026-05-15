# UI

This directory contains a minimal Flask + Jinja2 + HTMX starter for a chat web UI.

## Run

```bash
pip install -r requirements.txt
python run.py
```

### Run with mock data (default)

`MOCK_DATA_ENABLED` defaults to `true`, but you can set it explicitly:

```bash
MOCK_DATA_ENABLED=true python run.py
```

### Run without mock data

Use the real service implementation:

```bash
MOCK_DATA_ENABLED=false python run.py
```

## Structure

- `ui/__init__.py` - Flask app factory and routes
- `ui/templates/` - Jinja2 templates and partials
- `ui/static/` - Styles for the chat layout
