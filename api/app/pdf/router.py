from fastapi import APIRouter

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

# TODO(pdf): geração do PDF das telas a partir do mesmo ScreenPayload, com a mesma
# checagem de acesso das telas.
