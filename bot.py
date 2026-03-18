import asyncio
import base64
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

try:
    from patchright.async_api import async_playwright, BrowserContext, Page
except ImportError:
    from playwright.async_api import async_playwright, BrowserContext, Page
    print("⚠️  Patchright não instalado. Rode: pip install patchright && patchright install chrome")

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

SESSION_FILE = Path("session.json")
PORTAL_URL   = "https://portaldatransparencia.gov.br/pessoa-fisica/busca/lista"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CHROME_PROFILE = {
    "linux":  Path.home() / ".config/google-chrome",
    "darwin": Path.home() / "Library/Application Support/Google/Chrome",
    "win32":  Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data",
}

BENEFICIOS_ALVO = {"auxílio brasil", "auxílio emergencial", "bolsa família"}


# ---------------------------------------------------------------------------
# Helpers: comportamento humano
# ---------------------------------------------------------------------------

async def human_delay(page: Page, min_ms: int = 600, max_ms: int = 2200) -> None:
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def move_mouse_naturally(page: Page, locator) -> None:
    box = await locator.bounding_box()
    if not box:
        return
    await page.mouse.move(
        box["x"] + random.randint(-80, 80),
        box["y"] + random.randint(-60, 60),
        steps=random.randint(10, 20),
    )
    await human_delay(page, 150, 400)
    await page.mouse.move(
        box["x"] + box["width"] / 2,
        box["y"] + box["height"] / 2,
        steps=random.randint(8, 15),
    )


async def aceitar_cookies(page: Page) -> None:
    """Aceita cookies se o banner estiver visível."""
    try:
        btn = page.locator("button:has-text('Aceitar todos')")
        if await btn.is_visible(timeout=3000):
            await btn.click()
            await human_delay(page, 400, 800)
            print("    🍪 Cookies aceitos.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers: sessão e perfil
# ---------------------------------------------------------------------------

async def salvar_sessao(context: BrowserContext) -> None:
    storage = await context.storage_state()
    SESSION_FILE.write_text(json.dumps(storage))


def sessao_existente() -> bool:
    return SESSION_FILE.exists() and SESSION_FILE.stat().st_size > 0


def _chrome_profile_path() -> Path | None:
    path = CHROME_PROFILE.get(sys.platform)
    return path if (path and path.exists()) else None


def _copiar_perfil_chrome(origem: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="chrome_profile_"))
    perfil_default = origem / "Default"
    if perfil_default.exists():
        shutil.copytree(
            src=perfil_default,
            dst=tmp / "Default",
            ignore=shutil.ignore_patterns(
                "Cache", "Code Cache", "GPUCache",
                "CrashPad", "Crashpad",
                "SingletonLock", "SingletonCookie",
            ),
            dirs_exist_ok=True,
        )
    return tmp


# ---------------------------------------------------------------------------
# Construção do contexto
# ---------------------------------------------------------------------------

async def criar_contexto(p) -> tuple:
    chrome_path = _chrome_profile_path()
    if chrome_path:
        tmp_profile = await asyncio.to_thread(_copiar_perfil_chrome, chrome_path)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(tmp_profile),
            channel="chrome",
            headless=False,
            args=["--profile-directory=Default"],
            viewport={"width": 1280, "height": 900},
        )
        return context, None, tmp_profile

    browser = await p.chromium.launch(headless=False)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=USER_AGENT,
        storage_state=str(SESSION_FILE) if sessao_existente() else None,
    )
    return context, browser, None


# ---------------------------------------------------------------------------
# Coleta do cabeçalho do Panorama
# ---------------------------------------------------------------------------

async def coletar_cabecalho_panorama(page: Page) -> dict:
    """
    Extrai Nome, CPF e Localidade das caixas com borda azul da tela
    'Pessoa Física'. O portal renderiza cada campo como uma caixa que
    contém: linha 1 = label (ex: 'Nome'), linha 2 = valor.
    Exemplo visual:
        ┌──────────────────┐  ┌──────────┐  ┌───────────────────┐
        │ Nome             │  │ CPF      │  │ Localidade        │
        │ FULANO DA SILVA  │  │ ***123** │  │ CIDADE - UF       │
        └──────────────────┘  └──────────┘  └───────────────────┘
    """
    dados = {}
    try:
        dados = await page.evaluate("""
            () => {
                const resultado = {};

                // O portal usa caixas com borda onde o primeiro texto é o label
                // e o segundo é o valor — ambos dentro do mesmo elemento pai.
                // Varredura: para cada elemento que contém APENAS o texto do label,
                // pega o texto seguinte no mesmo bloco.
                const labels = {
                    'Nome':       'nome',
                    'CPF':        'cpf',
                    'Localidade': 'localidade',
                    'NIS':        'nis',
                };

                // Percorre todos os elementos pequenos que podem ser labels
                document.querySelectorAll('label, span, div, p').forEach(el => {
                    // Ignora elementos com filhos (queremos folhas ou quase-folhas)
                    const textoEl = (el.innerText || '').trim();
                    const chave = labels[textoEl];
                    if (!chave || resultado[chave]) return;

                    // Valor: próximo irmão, ou próximo filho do pai, ou filho direto
                    const candidatos = [
                        el.nextElementSibling,
                        el.parentElement?.children[
                            Array.from(el.parentElement.children).indexOf(el) + 1
                        ],
                    ];

                    for (const cand of candidatos) {
                        if (!cand) continue;
                        const val = (cand.innerText || cand.value || '').trim();
                        // Rejeita se o candidato é outro label conhecido
                        if (val && !labels[val] && val.length > 1) {
                            resultado[chave] = val;
                            break;
                        }
                    }

                    // Fallback: se o pai tem exatamente 2 linhas de texto,
                    // a segunda é o valor
                    if (!resultado[chave] && el.parentElement) {
                        const blocoTexto = (el.parentElement.innerText || '').trim();
                        const linhas = blocoTexto.split('\\n').map(l => l.trim()).filter(Boolean);
                        if (linhas.length === 2 && linhas[0] === textoEl) {
                            resultado[chave] = linhas[1];
                        }
                    }
                });

                return resultado;
            }
        """)
    except Exception as e:
        print(f"    ⚠️  Erro JS ao coletar dados da pessoa: {e}")

    # Fallback Playwright: tenta ler cada caixa diretamente pelo label visível
    if not any(dados.values()):
        for label, chave in [("Nome", "nome"), ("CPF", "cpf"), ("Localidade", "localidade")]:
            try:
                # Localiza o elemento que contém exatamente o label
                # e pega o texto do bloco pai (que inclui label + valor)
                bloco = page.locator(f"div:has(> *:text-is('{label}'))").first
                texto_bloco = (await bloco.inner_text()).strip()
                linhas = [l.strip() for l in texto_bloco.split("\n") if l.strip()]
                # Remove o label da lista e pega o que sobrar
                valor = next((l for l in linhas if l != label), "")
                if valor:
                    dados[chave] = valor
            except Exception:
                pass

    return {k: v for k, v in dados.items() if v and v.strip()}


# ---------------------------------------------------------------------------
# Coleta de parcelas na página de detalhamento
# ---------------------------------------------------------------------------

async def coletar_parcelas(page: Page) -> list[dict]:
    """
    Coleta todas as parcelas da página de detalhamento.
    - Aceita cookies se aparecerem
    - Ativa 'Paginação completa' para obter todas as linhas
    - Lê cabeçalhos dinamicamente (cada benefício tem colunas diferentes)
    """
    await aceitar_cookies(page)

    # Aguarda a tabela aparecer
    await page.wait_for_selector("table tbody tr td", timeout=30000)
    await human_delay(page, 800, 1500)

    # Ativa paginação completa se disponível
    try:
        btn_pag = page.get_by_text("Paginação completa", exact=False)
        if await btn_pag.is_visible(timeout=3000):
            await btn_pag.click()
            await human_delay(page, 2500, 4000)
            print("      📄 Paginação completa ativada.")
    except Exception:
        pass

    # Lê cabeçalhos da tabela
    headers = []
    try:
        ths = page.locator("table thead th, table thead td")
        for i in range(await ths.count()):
            txt = (await ths.nth(i).inner_text()).strip()
            norm = unicodedata.normalize("NFD", txt.lower())
            norm = "".join(c for c in norm if unicodedata.category(c) != "Mn")
            norm = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
            headers.append(norm or f"col_{i}")
    except Exception:
        pass

    if not headers:
        headers = ["mes_disponibilizacao", "parcela", "uf",
                   "municipio", "enquadramento", "valor", "observacao"]

    # Lê todas as linhas
    linhas = page.locator("table tbody tr")
    parcelas = []
    for j in range(await linhas.count()):
        colunas = linhas.nth(j).locator("td")
        n_cols = await colunas.count()
        if n_cols < 2:
            continue
        linha = {}
        for k in range(n_cols):
            chave = headers[k] if k < len(headers) else f"col_{k}"
            linha[chave] = (await colunas.nth(k).inner_text()).strip()
        parcelas.append(linha)

    return parcelas


# ---------------------------------------------------------------------------
# Consulta principal
# ---------------------------------------------------------------------------

async def consultar_portal(parametro: str) -> dict:
    print(f"🚀 Iniciando consulta para: {parametro}")

    async with async_playwright() as p:
        context, browser, tmp_profile = await criar_contexto(p)
        page = await context.new_page()

        try:
            # ------------------------------------------------------------------
            # 1. Acesso + cookies
            # ------------------------------------------------------------------
            print("🌐 Acessando Portal da Transparência...")
            await page.goto(PORTAL_URL, timeout=60000)
            await page.wait_for_load_state("networkidle")
            await aceitar_cookies(page)

            # ------------------------------------------------------------------
            # 2. Busca
            # ------------------------------------------------------------------
            print(f"⌨️  Pesquisando: {parametro}")
            campo = page.locator("#termo")
            await move_mouse_naturally(page, campo)
            await campo.fill("")
            await human_delay(page, 300, 700)
            for char in parametro:
                await campo.type(char, delay=random.randint(40, 120))

            botao = page.locator("#termo ~ button")
            await move_mouse_naturally(page, botao)
            await human_delay(page, 200, 500)
            await botao.click()
            await human_delay(page, 6000, 9000)

            # ------------------------------------------------------------------
            # 3. Sem resultados
            # ------------------------------------------------------------------
            if await page.get_by_text("Foram encontrados 0 resultados", exact=True).is_visible():
                msg = f"Foram encontrados 0 resultados para o termo {parametro}"
                print(f"⚠️  {msg}")
                return {"status": "erro", "erro": msg, "parametro_busca": parametro}

            # ------------------------------------------------------------------
            # 4. Entra no Panorama
            # ------------------------------------------------------------------
            print("🔗 Abrindo Panorama...")
            param_limpo = "".join(
                c for c in unicodedata.normalize("NFD", parametro)
                if unicodedata.category(c) != "Mn"
            )
            primeiro_nome = param_limpo.split()[0]
            link = page.get_by_role("link", name=re.compile(primeiro_nome, re.IGNORECASE)).first
            await move_mouse_naturally(page, link)
            await human_delay(page, 300, 600)
            await link.click(force=True)
            await human_delay(page, 10000, 14000)
            await page.wait_for_load_state("networkidle")
            await aceitar_cookies(page)

            # ------------------------------------------------------------------
            # 5. Cabeçalho (Apenas coleta os dados e salva a URL)
            # ------------------------------------------------------------------
            print("📋 Coletando cabeçalho do Panorama...")
            dados_panorama = await coletar_cabecalho_panorama(page)
            print(f"    → {dados_panorama}")

            # Salva a URL do Panorama para poder voltar após cada detalhe
            url_panorama = page.url
            print(f"    → URL Panorama salva: {url_panorama}")

            # ------------------------------------------------------------------
            # 6. Expande aba "Recebimentos de Recursos"
            # ------------------------------------------------------------------
            print("🖱️  Expandindo 'Recebimentos de Recursos'...")
            try:
                aba = page.get_by_text("RECEBIMENTOS DE RECURSOS", exact=False).first
                await aba.scroll_into_view_if_needed()
                await move_mouse_naturally(page, aba)
                await human_delay(page, 400, 800)
                await aba.click()
                
                # Aguarda a aba abrir e renderizar o conteúdo interno
                await human_delay(page, 3000, 5000)
            except Exception as e:
                print(f"⚠️  Aba: {e}")

            await aceitar_cookies(page)

            # ------------------------------------------------------------------
            # 6.5 Captura de evidência (AGORA NO LUGAR CERTO)
            # ------------------------------------------------------------------
            print("📸 Capturando evidência do Panorama (com a aba aberta)...")
            await page.evaluate("window.scrollTo(0, 0)") # Sobe a página para não cortar o topo
            screenshot_b64 = base64.b64encode(
                await page.screenshot(full_page=True)
            ).decode()

            # ------------------------------------------------------------------
            # 7. Identifica todos os botões "Detalhar" da página
            #    Cada botão corresponde a um benefício diferente.
            #    O clique navega para uma nova URL na mesma aba.
            #    Depois de coletar, volta para url_panorama e repete.
            # ------------------------------------------------------------------
            print("🔎 Coletando informações dos botões 'Detalhar'...")

            # Coleta informações de cada linha antes de clicar em qualquer coisa
            # para ter uma lista estável de hrefs/índices
            linhas_detalhar = await _mapear_botoes_detalhar(page)
            print(f"    → {len(linhas_detalhar)} botão(ões) 'Detalhar' encontrado(s).")

            detalhes_finais = []

            for idx, info_linha in enumerate(linhas_detalhar):
                nome_benef   = info_linha.get("nome_beneficio", f"Benefício {idx+1}")
                nome_norm    = nome_benef.lower()
                eh_alvo      = any(alvo in nome_norm for alvo in BENEFICIOS_ALVO)

                if not eh_alvo:
                    print(f"\n  → '{nome_benef}' fora do escopo, pulando.")
                    continue

                print(f"\n  ▶ {idx+1}/{len(linhas_detalhar)}: Detalhando '{nome_benef}'...")

                # Garante que estamos na página do Panorama
                if page.url != url_panorama:
                    print(f"    ↩  Voltando ao Panorama...")
                    await page.goto(url_panorama, timeout=60000)
                    await page.wait_for_load_state("networkidle")
                    await aceitar_cookies(page)
                    await human_delay(page, 3000, 5000)

                    # Re-expande a aba
                    try:
                        aba = page.get_by_text("RECEBIMENTOS DE RECURSOS", exact=False).first
                        if not await aba.locator("..").locator(".collapse.show, [aria-expanded='true']").count():
                            await aba.scroll_into_view_if_needed()
                            await aba.click()
                            await human_delay(page, 3000, 5000)
                    except Exception:
                        pass

                    await aceitar_cookies(page)

                # Clica no botão "Detalhar" correto (pelo índice, pois a lista pode mudar)
                btn = page.locator(
                    "button:has-text('Detalhar'), a:has-text('Detalhar')"
                ).nth(idx)

                await btn.scroll_into_view_if_needed()
                await move_mouse_naturally(page, btn)
                await human_delay(page, 400, 900)
                await btn.click(force=True)

                # Aguarda navegar para a página de detalhamento
                await page.wait_for_load_state("networkidle")
                await human_delay(page, 2000, 3500)
                print(f"    📄 URL detalhamento: {page.url}")

                # Aceita cookies na página de detalhamento
                await aceitar_cookies(page)

                # Coleta as parcelas
                parcelas = await coletar_parcelas(page)
                print(f"    ✅ {len(parcelas)} parcela(s) coletada(s).")
                await salvar_sessao(context)

                detalhes_finais.append({
                    "beneficio":      nome_benef,
                    "beneficiario":   {k: v for k, v in info_linha.items()
                                       if k != "nome_beneficio"},
                    "total_parcelas": len(parcelas),
                    "parcelas":       parcelas,
                })

            # ------------------------------------------------------------------
            # 8. JSON final
            # ------------------------------------------------------------------
            resultado = {
                "status":           "sucesso",
                "parametro_busca":  parametro,
                "dados_panorama":   dados_panorama,
                "beneficios":       detalhes_finais,
                "evidencia_base64": screenshot_b64,
            }
            print(f"\n✅ Concluído — {len(detalhes_finais)} benefício(s) coletado(s).")
            return resultado

        except Exception as e:
            print(f"❌ Erro crítico: {e}")
            return {
                "status":          "erro",
                "erro":            "Não foi possível retornar os dados no tempo de resposta solicitado",
                "detalhe":         str(e),
                "parametro_busca": parametro,
            }

        finally:
            print("🧹 Encerrando...")
            await context.close()
            if browser:
                await browser.close()
            if tmp_profile and tmp_profile.exists():
                shutil.rmtree(tmp_profile, ignore_errors=True)


# ---------------------------------------------------------------------------
# Mapeia os botões "Detalhar" e as informações de cada linha
# ---------------------------------------------------------------------------

async def _mapear_botoes_detalhar(page: Page) -> list[dict]:
    """
    Varre a seção de Recebimentos de Recursos e coleta, para cada botão
    'Detalhar' visível:
      - nome do benefício (tab/seção pai: Auxílio Emergencial, Bolsa Família…)
      - NIS, Nome e Valor da linha correspondente

    Retorna uma lista ordenada para uso posterior por índice.
    """
    resultado = []

    # Descobre o nome de cada seção/tab de benefício
    # O portal organiza assim:
    #   [Tab: Auxílio Emergencial]
    #     Tabela: | Detalhar | NIS | Nome | Valor Recebido |
    #   [Tab: Bolsa Família]
    #     Tabela: | Detalhar | NIS | Nome | Valor Recebido |

    botoes = page.locator("button:has-text('Detalhar'), a:has-text('Detalhar')")
    total  = await botoes.count()

    for i in range(total):
        btn  = botoes.nth(i)
        info = {"nome_beneficio": f"Benefício {i+1}"}

        # Tenta pegar o nome do benefício subindo no DOM
        try:
            nome_benef = await btn.evaluate("""
                el => {
                    // Sobe no DOM buscando uma tab/heading com o nome do programa
                    let node = el;
                    for (let d = 0; d < 15; d++) {
                        node = node.parentElement;
                        if (!node) break;
                        // Procura irmão anterior que seja um título de seção
                        let prev = node.previousElementSibling;
                        while (prev) {
                            const txt = (prev.innerText || '').trim();
                            if (txt && txt.length < 80 &&
                                (txt.includes('Auxílio') || txt.includes('Bolsa') ||
                                 txt.includes('BPC') || txt.includes('Seguro') ||
                                 txt.includes('FGTS') || txt.includes('Pé-de-Meia'))) {
                                return txt;
                            }
                            prev = prev.previousElementSibling;
                        }
                        // Procura dentro do próprio bloco pai
                        const headings = node.querySelectorAll(
                            'button.nav-link, a.nav-link, li.active a, ' +
                            '[role="tab"][aria-selected="true"], ' +
                            'h3, h4, .tab-title, .titulo-beneficio'
                        );
                        for (const h of headings) {
                            const txt = (h.innerText || '').trim();
                            if (txt && txt.length < 80) return txt;
                        }
                    }
                    return null;
                }
            """)
            if nome_benef:
                info["nome_beneficio"] = nome_benef.strip()
        except Exception:
            pass

        # Tenta pegar NIS, Nome, Valor da linha
        try:
            linha_dados = await btn.evaluate("""
                el => {
                    const tr = el.closest('tr');
                    if (!tr) return {};
                    const tds = Array.from(tr.querySelectorAll('td'));
                    const vals = tds.map(td => td.innerText.trim());
                    // cols: [btn_text, nis, nome, valor] ou variação
                    return {
                        nis:            vals[1] || '',
                        nome:           vals[2] || '',
                        valor_recebido: vals[3] || vals[vals.length - 1] || '',
                    };
                }
            """)
            info.update({k: v for k, v in linha_dados.items() if v})
        except Exception:
            pass

        print(f"    [{i}] {info.get('nome_beneficio')} | "
              f"NIS: {info.get('nis','?')} | "
              f"Nome: {info.get('nome','?')} | "
              f"Valor: {info.get('valor_recebido','?')}")
        resultado.append(info)

    return resultado


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parametro = sys.argv[1] if len(sys.argv) > 1 else "MARIA DA SILVA"
    resultado = asyncio.run(consultar_portal(parametro))

    output_file = Path("resultado.json")
    output_file.write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(f"\n📦 JSON salvo em: {output_file.resolve()}")

    if resultado.get("status") == "sucesso":
        for b in resultado.get("beneficios", []):
            print(f"  • {b['beneficio']}: {b['total_parcelas']} parcelas")
