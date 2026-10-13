"""Grava o openapi.json para o front gerar os tipos.

Uso (em api/): uv run python -m scripts.export_openapi [destino]   (padrão: openapi.json)
Não conecta no banco: o pool só abre no lifespan, que não roda aqui.
"""

import json
import os
import sys
from pathlib import Path

# Valores só para o app montar; nada aqui é usado de verdade.
os.environ.setdefault("DATABASE_URL", "postgresql://export@localhost/export")
os.environ.setdefault("AMBIENTE", "dev")
os.environ.setdefault("AUTH_FAKE", "1")

from app.main import app


def main() -> None:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    destino.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"OpenAPI gravado em {destino}")


if __name__ == "__main__":
    main()
