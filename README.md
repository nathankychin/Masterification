# Masterify

## Run locally

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Free hosting notes

This app is ready for a free Python hosting service such as Render or Railway.

### Recommended environment variables

- FLASK_SECRET_KEY: secret for Flask sessions
- DEV_CONSOLE_PASSWORD: admin console password
- COLLAB_PASSWORD: collaborator console password
- APP_NAME: set to Masterify
- APP_DOMAIN: set to your custom domain, such as masterify.app
- OPENAI_API_KEY: optional AI features
- PORT: port for hosting (default 5000)
- FLASK_DEBUG: set to 0 for production
- PREFERRED_URL_SCHEME: set to https

## Mobile access

The app includes mobile-friendly viewport settings and larger touch-friendly buttons so it can be used from phones and tablets on the web.

## New: Improved feedback & language prompts

- Question generation now produces two distinct prompts (core understanding + application).
- Language practice uses varied, regenerable phrases (server-side AI when `OPENAI_API_KEY` is set).
- Feedback rubrics are returned as structured JSON (displayed on the detailed revision page).
- Scoring is slightly board-aware (IGCSE/A-Levels/GCSE/O-levels) with configurable scoring weights.

To test locally run:

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
```

Trigger rebuild: bump
