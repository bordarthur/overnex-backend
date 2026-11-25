"""Gera os dados da Distribuidora Overnex e envia os Parquet ao Storage.

    python -m demo                      # envia (precisa de SUPABASE_URL e SUPABASE_SECRET_KEY)
    python -m demo --saida pasta        # só grava os arquivos na pasta, sem enviar
    python -m demo --saida pasta --data 2026-11-25

Códigos de saída: 0 tudo enviado; 1 algum arquivo falhou; 2 configuração inválida.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from demo.config import Config
from demo.gerar import gerar
from demo.storage import enviar, para_parquet


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo", description="Gera os dados da Distribuidora Overnex."
    )
    parser.add_argument("--saida", type=Path, help="grava os Parquet nesta pasta e não envia")
    parser.add_argument("--data", type=date.fromisoformat, help="data da carga (AAAA-MM-DD)")
    args = parser.parse_args()

    config: Config | None = None
    if args.saida is None:
        try:
            config = Config()
        except ValidationError as exc:
            campos = ", ".join(str(e["loc"][0]).upper() for e in exc.errors())
            print(f"configuração inválida: {campos}", file=sys.stderr)
            return 2
    hoje = args.data or (config.hoje() if config else date.today())

    tabelas = gerar(hoje)
    falhas = []
    for nome, tabela in tabelas.items():
        dados = para_parquet(tabela)
        tamanho = f"{tabela.num_rows:,} linhas, {len(dados) / 1e6:.2f} MB"
        if config is None:  # --saida: só grava na pasta
            args.saida.mkdir(parents=True, exist_ok=True)
            (args.saida / f"{nome}.parquet").write_bytes(dados)
            print(f"OK  {nome}: {tamanho}")
            continue
        caminho = f"{config.prefixo_demo}/{nome}.parquet"
        try:
            enviar(
                config.supabase_url,
                config.supabase_secret_key.get_secret_value(),
                config.supabase_bucket,
                caminho,
                dados,
            )
        except RuntimeError as exc:
            print(f"ERRO  {nome}: {exc}", file=sys.stderr)
            falhas.append(nome)
            continue
        print(f"OK  {nome}: {tamanho} -> {config.supabase_bucket}/{caminho}")
    print(f"Data da carga: {hoje:%d/%m/%Y}")
    if falhas:
        print(f"Falha em {len(falhas)} arquivo(s): {', '.join(falhas)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
