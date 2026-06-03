# TODO

## Make submissions public

Remove the API key gate from `POST /submit`. Anyone who can reach the server can submit a URL.

Changes:
- Delete `app/auth.py`
- Remove `Depends(require_api_key)` from `POST /submit` in `app/routers/submit.py`
- Remove `API_KEY` from `config.py`, `.env.example`, `CLAUDE.md`, and `deploy.sh`
- Remove the API key input field and localStorage logic from `app/templates/submit.html`

---

## Session identity (groundwork for future submitter privileges)

On every `POST /submit`, assign the client a persistent session UUID. Store it in a cookie and record it on the `videos` row. This lets a future auth layer identify "the person who submitted this" without building a full user system now.

### Schema change

Add `submitter_id` column to `videos`:

```
submitter_id  String(36) nullable  # UUID4, set at submit time, never rotated
```

### Behavior

- On `POST /submit`: read `submitter_id` cookie from the request.
  - If present and valid UUID4, use it.
  - If absent or invalid, generate `uuid.uuid4()`.
- Write `submitter_id` to the `videos` row.
- Set `Set-Cookie: submitter_id=<uuid>; HttpOnly; SameSite=Lax; Max-Age=31536000` on the response.

### What this enables later

Promote a known UUID to owner/admin (e.g. via a one-time env var `OWNER_SESSION_ID`) to gate privileged actions (delete, re-process, admin view) without a full user table.
