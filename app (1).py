"""
=====================================================================
 APP: Gestão e Acompanhamento de Treinamentos de Colaboradores
 STACK: Streamlit + gspread (Google Sheets) + Plotly
=====================================================================

Como usar:
1. Duplique este arquivo (ou apenas troque a variável NOME_ABA_REGISTRO
   abaixo) para gerar uma versão do app para cada Registro (01, 02, 03).
2. Configure o arquivo `.streamlit/secrets.toml` com as credenciais da
   Service Account do Google Cloud e o ID/nome da planilha (veja o
   modelo enviado junto com este código).
3. Rode com: streamlit run app.py
"""

import re
import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# =====================================================================
# 1. CONFIGURAÇÕES GERAIS (parametrize aqui)
# =====================================================================

# --> Altere esta variável para "Registro_01", "Registro_02" ou "Registro_03"
#     ao duplicar o app para cada grupo de registro.
NOME_ABA_REGISTRO = "Registro_01"

# Nome (ou ID) da planilha do Google Sheets. Pode vir do secrets.toml.
# Prioridade: secrets["gsheets"]["spreadsheet_id"] > secrets["gsheets"]["spreadsheet_name"]
ABA_TREINAMENTOS = "Treinamentos"
ABA_COLABORADORES = "Colaboradores"

# Tempo de cache dos dados lidos do Sheets (em segundos)
CACHE_TTL_SEGUNDOS = 120

# Lista oficial dos 6 status possíveis (ordem importa para o selectbox)
LISTA_STATUS = [
    "0 - Não Realizado",
    "1 - Conheceu",
    "2 - Observou",
    "3 - Praticou acompanhado",
    "4 - Realizou sozinho",
    "5 - Validado",
]

STATUS_NAO_REALIZADO = "0 - Não Realizado"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

st.set_page_config(
    page_title=f"Treinamentos - {NOME_ABA_REGISTRO}",
    page_icon="📋",
    layout="wide",
)


# =====================================================================
# 2. CONEXÃO COM O GOOGLE SHEETS
# =====================================================================

@st.cache_resource(show_spinner=False)
def conectar_planilha():
    """
    Cria e retorna o objeto de conexão (Spreadsheet) usando uma
    Service Account autenticada via st.secrets.

    Requer em .streamlit/secrets.toml:
        [gcp_service_account]
        type = "service_account"
        ... (demais campos do JSON da service account)

        [gsheets]
        spreadsheet_id = "ID_DA_PLANILHA"   # recomendado
        # ou, alternativamente:
        # spreadsheet_name = "Nome da Planilha"
    """
    credenciais = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    cliente = gspread.authorize(credenciais)

    if "spreadsheet_id" in st.secrets.get("gsheets", {}):
        planilha = cliente.open_by_key(st.secrets["gsheets"]["spreadsheet_id"])
    else:
        planilha = cliente.open(st.secrets["gsheets"]["spreadsheet_name"])

    return planilha


# =====================================================================
# 3. LEITURA DOS DADOS (com cache)
# =====================================================================

@st.cache_data(ttl=CACHE_TTL_SEGUNDOS, show_spinner="Carregando treinamentos...")
def carregar_treinamentos(_planilha):
    """Lê a aba 'Treinamentos' (ID, Nome, Grupo)."""
    aba = _planilha.worksheet(ABA_TREINAMENTOS)
    dados = aba.get_all_records()
    df = pd.DataFrame(dados)
    df["ID_Treinamento"] = df["ID_Treinamento"].astype(str).str.zfill(3)
    return df


@st.cache_data(ttl=CACHE_TTL_SEGUNDOS, show_spinner="Carregando colaboradores...")
def carregar_colaboradores(_planilha):
    """Lê a aba 'Colaboradores' e filtra apenas os do registro configurado."""
    aba = _planilha.worksheet(ABA_COLABORADORES)
    dados = aba.get_all_records()
    df = pd.DataFrame(dados)
    df["ID_Colaborador"] = df["ID_Colaborador"].astype(str)
    # Mantém apenas colaboradores pertencentes à aba de registro atual
    df = df[df["Registro"] == NOME_ABA_REGISTRO].copy()
    return df


@st.cache_data(ttl=CACHE_TTL_SEGUNDOS, show_spinner="Carregando registros de treinamento...")
def carregar_registros(_planilha):
    """
    Lê a aba de registro configurada em NOME_ABA_REGISTRO.
    Retorna o DataFrame junto com o número da linha real na planilha
    (para permitir atualização direta célula a célula depois).
    """
    aba = _planilha.worksheet(NOME_ABA_REGISTRO)
    valores = aba.get_all_records()
    df = pd.DataFrame(valores)
    df["ID_Colaborador"] = df["ID_Colaborador"].astype(str)
    df["ID_Treinamento"] = df["ID_Treinamento"].astype(str).str.zfill(3)
    # Linha 1 = cabeçalho, então a primeira linha de dado é a linha 2
    df["_linha_planilha"] = df.index + 2
    return df


def limpar_cache_e_recarregar():
    """Limpa o cache de dados e força releitura do Google Sheets."""
    carregar_treinamentos.clear()
    carregar_colaboradores.clear()
    carregar_registros.clear()


# =====================================================================
# 4. FUNÇÕES DE APOIO / REGRAS DE NEGÓCIO
# =====================================================================

def status_e_realizado(status: str) -> bool:
    """Retorna True se o status representa um treinamento 'Realizado' (1 a 5)."""
    return status != STATUS_NAO_REALIZADO


def montar_tabela_colaborador(df_treinamentos, df_registros, id_colaborador):
    """
    Junta a lista mestre de treinamentos com os registros do colaborador
    selecionado, garantindo que os 90 treinamentos apareçam mesmo que
    algum ainda não tenha registro salvo (assume '0 - Não Realizado').
    """
    df_reg_colab = df_registros[df_registros["ID_Colaborador"] == id_colaborador].copy()

    df_tabela = df_treinamentos.merge(
        df_reg_colab[["ID_Treinamento", "Status", "_linha_planilha"]],
        on="ID_Treinamento",
        how="left",
    )
    df_tabela["Status"] = df_tabela["Status"].fillna(STATUS_NAO_REALIZADO)
    return df_tabela


def calcular_metricas(df_tabela):
    """Calcula os cards de métricas gerais do colaborador."""
    total = len(df_tabela)
    realizados = df_tabela["Status"].apply(status_e_realizado).sum()
    nao_realizados = total - realizados
    percentual = (realizados / total * 100) if total > 0 else 0
    return total, int(realizados), int(nao_realizados), percentual


def calcular_dados_por_grupo(df_tabela, visao_detalhada: bool):
    """
    Agrega os dados por Grupo de treinamento.
    - Se visao_detalhada = False: retorna % Realizado vs % Não Realizado por grupo.
    - Se visao_detalhada = True: retorna contagem dos 6 status por grupo.
    """
    if not visao_detalhada:
        df_temp = df_tabela.copy()
        df_temp["Situação"] = df_temp["Status"].apply(
            lambda s: "Realizado" if status_e_realizado(s) else "Não Realizado"
        )
        resumo = (
            df_temp.groupby(["Grupo", "Situação"])
            .size()
            .reset_index(name="Quantidade")
        )
        totais_grupo = df_temp.groupby("Grupo").size().reset_index(name="Total")
        resumo = resumo.merge(totais_grupo, on="Grupo")
        resumo["Percentual"] = (resumo["Quantidade"] / resumo["Total"] * 100).round(1)
        return resumo
    else:
        resumo = (
            df_tabela.groupby(["Grupo", "Status"])
            .size()
            .reset_index(name="Quantidade")
        )
        return resumo


def calcular_pendentes_por_grupo(df_tabela):
    """Retorna a quantidade de treinamentos '0 - Não Realizado' por grupo."""
    df_pendentes = df_tabela[df_tabela["Status"] == STATUS_NAO_REALIZADO]
    resumo = df_pendentes.groupby("Grupo").size().reset_index(name="Pendentes")
    return resumo


def extrair_numero_grupo(nome_grupo: str):
    """Extrai o número contido no nome do grupo (ex: 'Grupo 10' -> 10) para
    permitir ordenação natural das colunas (evita 'Grupo 10' antes de 'Grupo 2')."""
    numeros = re.findall(r"\d+", str(nome_grupo))
    return int(numeros[0]) if numeros else 9999


def montar_base_geral(df_treinamentos, df_registros, df_colaboradores):
    """
    Monta o produto cartesiano Colaborador x Treinamento (apenas para os
    colaboradores filtrados) e cruza com os registros existentes.
    Colaboradores/treinamentos sem registro salvo são tratados como
    '0 - Não Realizado', igual à regra usada na visão individual.
    """
    df_colab_ids = df_colaboradores[["ID_Colaborador", "Nome"]].drop_duplicates().copy()
    df_treinos_min = df_treinamentos[["ID_Treinamento", "Grupo"]].drop_duplicates().copy()

    df_colab_ids["_chave"] = 1
    df_treinos_min["_chave"] = 1
    base = df_colab_ids.merge(df_treinos_min, on="_chave").drop(columns="_chave")

    base = base.merge(
        df_registros[["ID_Colaborador", "ID_Treinamento", "Status"]],
        on=["ID_Colaborador", "ID_Treinamento"],
        how="left",
    )
    base["Status"] = base["Status"].fillna(STATUS_NAO_REALIZADO)
    base["Realizado"] = base["Status"].apply(status_e_realizado)
    return base


def montar_matriz_geral(df_treinamentos, df_registros, df_colaboradores):
    """
    Constrói a tabela 'Visão Geral': uma linha por colaborador e uma
    coluna por grupo de treinamento, mostrando quantos treinamentos
    faltam (status 0) em cada grupo, ou 'Completo' quando não há pendências.
    Inclui também colunas de resumo (Total, Realizados, % Progresso).
    """
    base = montar_base_geral(df_treinamentos, df_registros, df_colaboradores)

    # --- Resumo geral por colaborador (independente de grupo) ---
    resumo = base.groupby("Nome").agg(
        Total_Treinamentos=("Realizado", "size"),
        Total_Realizados=("Realizado", "sum"),
    ).reset_index()
    resumo["Total_Nao_Realizados"] = (
        resumo["Total_Treinamentos"] - resumo["Total_Realizados"]
    )
    resumo["Percentual"] = (
        resumo["Total_Realizados"] / resumo["Total_Treinamentos"] * 100
    ).round(1)

    # --- Pendências por colaborador x grupo ---
    pendentes_por_grupo = (
        base[~base["Realizado"]]
        .groupby(["Nome", "Grupo"])
        .size()
        .reset_index(name="Pendentes")
    )

    grupos_ordenados = sorted(
        base["Grupo"].drop_duplicates().tolist(), key=extrair_numero_grupo
    )

    pivot_pendentes = pendentes_por_grupo.pivot(
        index="Nome", columns="Grupo", values="Pendentes"
    ).reindex(columns=grupos_ordenados).fillna(0).astype(int)

    # Formata cada célula: "✅ Completo" ou "⚠️ X pendente(s)"
    def formatar_celula(qtd):
        return "✅ Completo" if qtd == 0 else f"⚠️ {qtd} pendente(s)"

    pivot_formatado = pivot_pendentes.map(formatar_celula)
    pivot_formatado = pivot_formatado.reindex(resumo["Nome"]).reset_index()

    matriz_final = resumo.merge(pivot_formatado, on="Nome", how="left")
    matriz_final = matriz_final.rename(columns={
        "Nome": "Colaborador",
        "Total_Treinamentos": "Total",
        "Total_Realizados": "Realizados",
        "Total_Nao_Realizados": "Não Realizados",
        "Percentual": "% Progresso",
    })

    colunas_ordenadas = (
        ["Colaborador", "Total", "Realizados", "Não Realizados", "% Progresso"]
        + grupos_ordenados
    )
    return matriz_final[colunas_ordenadas]


# =====================================================================
# 5. GRAVAÇÃO NO GOOGLE SHEETS
# =====================================================================

def salvar_alteracoes(planilha, df_editado, df_registros_original, id_colaborador):
    """
    Compara a tabela editada pelo usuário com os dados originais e
    atualiza SOMENTE as células de Status que mudaram, usando
    atualização em lote (batch_update) para economizar chamadas de API.

    Caso o colaborador ainda não tenha uma linha para determinado
    treinamento na aba de registro, uma nova linha é adicionada (append).
    """
    aba = planilha.worksheet(NOME_ABA_REGISTRO)

    df_reg_colab = df_registros_original[
        df_registros_original["ID_Colaborador"] == id_colaborador
    ].copy()

    # Mapa ID_Treinamento -> número da linha na planilha (se já existir)
    mapa_linhas = dict(zip(df_reg_colab["ID_Treinamento"], df_reg_colab["_linha_planilha"]))
    # Mapa ID_Treinamento -> status atual salvo na planilha
    mapa_status_atual = dict(zip(df_reg_colab["ID_Treinamento"], df_reg_colab["Status"]))

    celulas_para_atualizar = []
    novas_linhas = []
    qtd_alteracoes = 0

    for _, linha in df_editado.iterrows():
        id_treinamento = linha["ID_Treinamento"]
        status_novo = linha["Status"]
        status_antigo = mapa_status_atual.get(id_treinamento)

        if status_antigo is None:
            # Treinamento sem registro prévio -> cria nova linha ao final
            novas_linhas.append([id_colaborador, id_treinamento, status_novo])
            qtd_alteracoes += 1
        elif status_novo != status_antigo:
            # Já existe registro e o status mudou -> atualiza célula (coluna C = Status)
            num_linha = mapa_linhas[id_treinamento]
            celulas_para_atualizar.append(
                gspread.cell.Cell(row=num_linha, col=3, value=status_novo)
            )
            qtd_alteracoes += 1

    if celulas_para_atualizar:
        aba.update_cells(celulas_para_atualizar, value_input_option="USER_ENTERED")

    if novas_linhas:
        aba.append_rows(novas_linhas, value_input_option="USER_ENTERED")

    return qtd_alteracoes


# =====================================================================
# 6. APLICAÇÃO PRINCIPAL (INTERFACE STREAMLIT)
# =====================================================================

def main():
    st.title("📋 Gestão e Acompanhamento de Treinamentos")
    st.caption(f"Aba de registro ativa: **{NOME_ABA_REGISTRO}**")

    # --- Conexão e carregamento inicial ---
    try:
        planilha = conectar_planilha()
    except Exception as erro:
        st.error(
            "Não foi possível conectar ao Google Sheets. "
            "Verifique as credenciais em `st.secrets`."
        )
        st.exception(erro)
        st.stop()

    df_treinamentos = carregar_treinamentos(planilha)
    df_colaboradores = carregar_colaboradores(planilha)
    df_registros = carregar_registros(planilha)

    # =================================================================
    # SIDEBAR - FILTROS GLOBAIS
    # =================================================================
    with st.sidebar:
        st.header("🔎 Filtros")

        if st.button("🔄 Recarregar dados da planilha", use_container_width=True):
            limpar_cache_e_recarregar()
            st.rerun()

        situacao_filtro = st.radio(
            "Situação do colaborador",
            options=["Ativo", "Afastado"],
            index=0,
            horizontal=True,
        )

        df_colab_filtrado = df_colaboradores[
            df_colaboradores["Situação"] == situacao_filtro
        ]

        if df_colab_filtrado.empty:
            st.warning(f"Nenhum colaborador '{situacao_filtro}' encontrado neste registro.")
            st.stop()

        opcoes_colaborador = dict(
            zip(df_colab_filtrado["Nome"], df_colab_filtrado["ID_Colaborador"])
        )
        nome_selecionado = st.selectbox(
            "Colaborador", options=sorted(opcoes_colaborador.keys())
        )
        id_colaborador_selecionado = opcoes_colaborador[nome_selecionado]

        st.divider()
        visao_detalhada = st.toggle(
            "Visão detalhada (6 status)",
            value=False,
            help="Desligado: agrupa em Realizado x Não Realizado. "
                 "Ligado: mostra os 6 status individualmente.",
        )

    # =================================================================
    # ABAS PRINCIPAIS: Dashboard individual x Visão Geral (todos colaboradores)
    # =================================================================
    aba_geral, aba_individual = st.tabs(["🌍 Visão Geral", "👤 Colaborador"])

    # -----------------------------------------------------------------
    # ABA 1 - DASHBOARD E EDIÇÃO DO COLABORADOR SELECIONADO
    # -----------------------------------------------------------------
    with aba_individual:
        # Monta a tabela consolidada dos 90 treinamentos do colaborador selecionado
        df_tabela_colab = montar_tabela_colaborador(
            df_treinamentos, df_registros, id_colaborador_selecionado
        )

        st.subheader(f"📊 Visão Geral — {nome_selecionado}")

        total, realizados, nao_realizados, percentual = calcular_metricas(df_tabela_colab)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total de Treinamentos", total)
        col2.metric("Realizados (1 a 5)", realizados)
        col3.metric("Não Realizados (0)", nao_realizados)
        col4.metric("Progresso Geral", f"{percentual:.1f}%")

        st.progress(min(percentual / 100, 1.0))

        st.divider()

        grafico_col1, grafico_col2 = st.columns(2)

        # ---- Gráfico 1: % Realizado vs Não Realizado (ou detalhado) por grupo ----
        with grafico_col1:
            st.markdown("**Percentual de Realização por Grupo**")
            dados_grupo = calcular_dados_por_grupo(df_tabela_colab, visao_detalhada)

            if not visao_detalhada:
                fig1 = px.bar(
                    dados_grupo,
                    x="Grupo",
                    y="Percentual",
                    color="Situação",
                    barmode="stack",
                    text="Percentual",
                    color_discrete_map={
                        "Realizado": "#2E7D32",
                        "Não Realizado": "#C62828",
                    },
                    labels={"Percentual": "% do Grupo"},
                )
                fig1.update_traces(texttemplate="%{text:.1f}%", textposition="inside")
                fig1.update_layout(yaxis_range=[0, 100])
            else:
                fig1 = px.bar(
                    dados_grupo,
                    x="Grupo",
                    y="Quantidade",
                    color="Status",
                    barmode="stack",
                    category_orders={"Status": LISTA_STATUS},
                )

            st.plotly_chart(fig1, use_container_width=True)

        # ---- Gráfico 2: Quantidade de pendentes por grupo ----
        with grafico_col2:
            st.markdown("**Treinamentos Pendentes (Status 0) por Grupo**")
            dados_pendentes = calcular_pendentes_por_grupo(df_tabela_colab)

            if dados_pendentes.empty:
                st.success("Nenhum treinamento pendente para este colaborador! 🎉")
            else:
                fig2 = px.bar(
                    dados_pendentes,
                    x="Grupo",
                    y="Pendentes",
                    text="Pendentes",
                    color_discrete_sequence=["#C62828"],
                )
                fig2.update_traces(textposition="outside")
                st.plotly_chart(fig2, use_container_width=True)

        st.divider()

        # =============================================================
        # SEÇÃO - EDIÇÃO DOS TREINAMENTOS
        # =============================================================
        st.subheader("✏️ Edição dos Treinamentos")
        st.caption(
            "Altere o status diretamente na tabela e clique em "
            "**Salvar Alterações** para gravar no Google Sheets."
        )

        df_para_editar = df_tabela_colab[
            ["ID_Treinamento", "Nome_Treinamento", "Grupo", "Status"]
        ].sort_values("ID_Treinamento").reset_index(drop=True)

        df_editado = st.data_editor(
            df_para_editar,
            column_config={
                "ID_Treinamento": st.column_config.TextColumn("ID", disabled=True),
                "Nome_Treinamento": st.column_config.TextColumn("Treinamento", disabled=True),
                "Grupo": st.column_config.TextColumn("Grupo", disabled=True),
                "Status": st.column_config.SelectboxColumn(
                    "Status",
                    options=LISTA_STATUS,
                    required=True,
                ),
            },
            hide_index=True,
            use_container_width=True,
            key=f"editor_{id_colaborador_selecionado}",
        )

        if st.button("💾 Salvar Alterações", type="primary"):
            with st.spinner("Gravando alterações no Google Sheets..."):
                try:
                    qtd_alteracoes = salvar_alteracoes(
                        planilha, df_editado, df_registros, id_colaborador_selecionado
                    )
                    limpar_cache_e_recarregar()
                except Exception as erro:
                    st.error("Ocorreu um erro ao salvar as alterações.")
                    st.exception(erro)
                    st.stop()

            if qtd_alteracoes > 0:
                st.success(
                    f"✅ {qtd_alteracoes} alteração(ões) salva(s) com sucesso às "
                    f"{datetime.now().strftime('%H:%M:%S')}!"
                )
                st.rerun()
            else:
                st.info("Nenhuma alteração detectada para salvar.")

    # -----------------------------------------------------------------
    # ABA 2 - VISÃO GERAL: TODOS OS COLABORADORES x GRUPOS DE TREINAMENTO
    # -----------------------------------------------------------------
    with aba_geral:
        st.subheader(f"🌍 Visão Geral — Colaboradores '{situacao_filtro}'")
        st.caption(
            "Cada linha é um colaborador e cada coluna é um grupo de treinamento, "
            "mostrando quantos treinamentos ainda faltam naquele grupo "
            "(ou 'Completo' quando todos já foram realizados)."
        )

        matriz_geral = montar_matriz_geral(
            df_treinamentos, df_registros, df_colab_filtrado
        )

        # Métricas consolidadas do grupo de colaboradores filtrado
        total_colab = len(matriz_geral)
        media_progresso = matriz_geral["% Progresso"].mean() if total_colab > 0 else 0
        qtd_100pct = (matriz_geral["% Progresso"] == 100).sum()

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Colaboradores nesta visão", total_colab)
        col_b.metric("Progresso médio do grupo", f"{media_progresso:.1f}%")
        col_c.metric("Colaboradores 100% concluídos", int(qtd_100pct))

        # Busca rápida por nome (opcional) para listas grandes
        busca_nome = st.text_input(
            "🔍 Buscar colaborador pelo nome", placeholder="Digite parte do nome..."
        )
        if busca_nome:
            matriz_exibicao = matriz_geral[
                matriz_geral["Colaborador"].str.contains(busca_nome, case=False, na=False)
            ]
        else:
            matriz_exibicao = matriz_geral

        st.dataframe(
            matriz_exibicao,
            column_config={
                "% Progresso": st.column_config.ProgressColumn(
                    "% Progresso", min_value=0, max_value=100, format="%.1f%%"
                ),
            },
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            "💡 Dica: use a busca acima para localizar um colaborador específico "
            "sem precisar trocar o filtro da barra lateral."
        )


if __name__ == "__main__":
    main()
