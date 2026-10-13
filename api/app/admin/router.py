from fastapi import APIRouter

router = APIRouter(prefix="/api/admin", tags=["admin"])

# TODO(admin): cadastros (clientes, empresas, perfis, usuários e vínculos, regras),
# com papel 'admin', políticas RLS de escrita e registro em seg.log_alteracao.
