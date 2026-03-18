from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from bot import consultar_portal # Importamos a função que você acabou de testar!

app = FastAPI(
    title="API RPA - Most - Portal da Transparência",
    description="Interface para automação de consulta de beneficiários de programas sociais.",
    version="1.0.0"
)

# Definimos o formato do que a API vai receber (Requisito do Desafio)
class ConsultaRequest(BaseModel):
    nome_ou_cpf: str
    filtro_social: bool = False

@app.get("/")
def home():
    return {"mensagem": "API Online. Acesse /docs para documentação Swagger."}

@app.post("/consultar")
async def executar_consulta(request: ConsultaRequest):
    """
    Endpoint que dispara o robô Playwright para consultar o Portal da Transparência.
    """
    print(f"🚀 Recebida requisição para: {request.nome_ou_cpf}")
    
    # Chama o robô que construímos no bot.py
    resultado = await consultar_portal(request.nome_ou_cpf)
    
    return resultado