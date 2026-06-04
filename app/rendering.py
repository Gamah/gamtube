from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

import jinja2
from fastapi.responses import HTMLResponse

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    env.filters["qe"] = quote_plus
    return env


def render(name: str, **ctx: object) -> HTMLResponse:
    return HTMLResponse(_env().get_template(name).render(**ctx))

