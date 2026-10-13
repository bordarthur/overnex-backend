# Overnex

| Pasta | O que é |
|---|---|
| [api/](api/) | API em FastAPI: a única peça que fala com o banco para servir o front |
| [supabase/](supabase/) | banco: migrations, seed e testes pgTAP |
| [jobs/snapshot/](jobs/snapshot/) | snapshot diário Azure SQL → Parquet no Storage |
| [jobs/carga/](jobs/carga/) | carga diária Parquet do Storage → mart |
| [.github/workflows/diario.yml](.github/workflows/diario.yml) | agenda: snapshot às 9h de Fortaleza e, se der certo, a carga |

Para subir local, veja [supabase/README.md](supabase/README.md) e depois [api/README.md](api/README.md).
