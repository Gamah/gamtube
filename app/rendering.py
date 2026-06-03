from functools import lru_cache
from pathlib import Path

import jinja2
from fastapi.responses import HTMLResponse

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )


def render(name: str, **ctx: object) -> HTMLResponse:
    return HTMLResponse(_env().get_template(name).render(**ctx))
