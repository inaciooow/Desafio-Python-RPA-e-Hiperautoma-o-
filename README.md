### 🎥 Demonstração em Vídeo
**[Clique aqui para assistir ao vídeo da automação e da integração com o Make.com rodando na prática](https://youtu.be/i7RDPDdKd_c?si=xAAxSspkOD0svHJD)**

## 🚀 Guia de Configuração e Execução

Este guia orienta passo a passo como preparar o ambiente, instalar as dependências e executar a API de RPA localmente para testes.

### 1. Pré-requisitos
* **Python 3.10+** instalado na máquina.
* **Git** para clonar o repositório.

### 2. Clonando e Preparando o Ambiente
Para isolar as dependências do projeto, é altamente recomendado o uso de um ambiente virtual (`venv`). Abra o terminal e execute:

```bash
# Clone o repositório
git clone [https://github.com/inaciooow/Desafio-Python-RPA-e-Hiperautoma-o-.git](https://github.com/inaciooow/Desafio-Python-RPA-e-Hiperautoma-o-.git)
cd Desafio-Python-RPA-e-Hiperautoma-o-

# Crie o ambiente virtual
python -m venv venv

# Ative o ambiente virtual
# No Mac/Linux:
source venv/bin/activate
# No Windows:
venv\Scripts\activate
```

### 3. Instalação das Dependências
Com o ambiente ativado, instale as bibliotecas requeridas e baixe os navegadores otimizados do Patchright (necessários para a evasão do Cloudflare):

```bash
# Instala os pacotes Python (FastAPI, Uvicorn, Pydantic, Patchright)
pip install -r requirements.txt

# Instala a versão modificada do Chrome via Patchright
patchright install chrome
```

### 4. Iniciando o Servidor (API)
Suba a aplicação utilizando o Uvicorn. O parâmetro `--reload` permite o recarregamento automático em caso de alterações no código.

```bash
uvicorn main:app --reload
```

*O servidor estará online e escutando na porta padrão: http://127.0.0.1:8000.*

### 5. Testando a Solução via Swagger UI (OpenAPI)
A API conta com documentação interativa nativa. Para testar o robô sem a necessidade de ferramentas externas como Postman:

1. Abra o navegador e acesse a documentação: **http://127.0.0.1:8000/docs**
2. Expanda o endpoint `POST /consultar`.
3. Clique no botão **"Try it out"** (canto superior direito da rota).
4. No campo **Request body**, insira o payload de teste. Exemplo:

```json
{
  "nome_ou_cpf": "MARIA DA SILVA",
  "filtro_social": true
}
```

5. Clique no botão azul **"Execute"**.
6. **Resultado:** O robô processará a requisição e a própria interface do Swagger exibirá a resposta `HTTP 200` com o JSON final estruturado (incluindo o status, dados cadastrais, lista detalhada de benefícios e a evidência em Base64).
