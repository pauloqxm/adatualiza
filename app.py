import os
import re
import unicodedata
import textwrap
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any

import pandas as pd
import streamlit as st

# =========================
# Configurações e Constantes
# =========================
APP_TITLE = "Atualização de Cadastro da Igreja"
TZ = ZoneInfo("America/Fortaleza")
LOGO_PATH = os.path.join("data", "logo_ad.jpg")

# Google Sheets Config
SPREADSHEET_ID = "1IUXWrsoBC58-Pe_6mcFQmzgX1xm6GDYvjP1Pd6FH3D0"
WORKSHEET_GID = 1191582738

REQUIRED_COLS = [
    "membro_id", "cod_membro", "data_nasc", "nome_mae", "nome_completo",
    "cpf", "whatsapp_telefone", "bairro_distrito", "endereco", "nome_pai",
    "nacionalidade", "naturalidade", "estado_civil", "data_batismo",
    "congregacao", "atualizado",
]

REQUIRED_FIELDS = {
    "nome_completo": "Nome completo",
    "cpf": "CPF",
    "data_nasc": "Data de nascimento",
    "whatsapp_telefone": "WhatsApp/Telefone",
    "bairro_distrito": "Bairro/Distrito",
    "endereco": "Endereço",
    "nome_mae": "Nome da mãe",
    "estado_civil": "Estado civil",
    "congregacao": "Congregação",
}

BAIRROS_DISTRITOS = [
    "Argentina Siqueira", "Belém", "Berilândia", "Centro", "Cohab", "Conjunto Esperança",
    "Damião Carneiro", "Depósito", "Distrito Industrial", "Duque De Caxias",
    "Edmilson Correia De Vasconcelos", "Encantado", "Jaime Lopes", "José Aurélio Câmara",
    "Lacerda", "Manituba", "Maravilha", "Monteiro De Morais", "Nenelândia", "Passagem",
    "Paus Branco", "Salviano Carlos", "São Miguel", "Sede Rural", "Uruquê",
    "Vila Betânia", "Vila São Paulo"
]

DROPDOWN_FIELDS = ["congregacao", "nacionalidade", "estado_civil"]

# =========================
# Autenticação e Conexão
# =========================
@st.cache_resource
def get_gspread_client():
    """Gerencia a conexão com o Google Sheets com cache de recurso."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        st.error("Dependências ausentes. Instale gspread e google-auth.")
        return None

    creds_info = None
    # 1. Tenta st.secrets
    if "gcp_service_account" in st.secrets:
        creds_info = st.secrets["gcp_service_account"]
    # 2. Tenta arquivo local
    elif os.path.exists("service_account.json"):
        with open("service_account.json", "r", encoding="utf-8") as f:
            creds_info = json.load(f)
    # 3. Tenta variável de ambiente
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        env_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                creds_info = json.load(f)

    if not creds_info:
        st.error("Credenciais do Google não encontradas.")
        return None

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    
    try:
        credentials = Credentials.from_service_account_info(creds_info, scopes=scope)
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"Erro ao autorizar: {e}")
        return None

def open_worksheet(client, spreadsheet_id: str, gid: int):
    """Abre a aba específica pelo GID."""
    try:
        sh = client.open_by_key(spreadsheet_id)
        for ws in sh.worksheets():
            if str(ws.id) == str(gid):
                return ws
        st.error(f"Aba com GID {gid} não encontrada.")
    except Exception as e:
        st.error(f"Erro ao abrir planilha: {e}")
    return None

# =========================
# Utilitários de Formatação e Validação
# =========================
def norm_text(s: Any) -> str:
    """Normaliza texto para busca (sem acentos, minúsculo)."""
    if pd.isna(s) or s is None:
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower())

def first_token(s: Any) -> str:
    """Retorna a primeira palavra normalizada."""
    normalized = norm_text(s)
    return normalized.split(" ", 1)[0] if normalized else ""

def only_digits(s: Any) -> str:
    """Remove tudo que não for dígito."""
    return re.sub(r"\D+", "", str(s or ""))

def parse_date_any(v: Any) -> Optional[date]:
    """Tenta converter diversos formatos para objeto date."""
    if pd.isna(v) or v is None: return None
    if isinstance(v, (date, datetime)): return v.date() if hasattr(v, 'date') else v
    
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def format_cpf(v: Any) -> str:
    d = only_digits(v)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else str(v or "").strip()

def format_phone_br(v: Any) -> str:
    d = only_digits(v)
    return f"({d[:2]}) {d[2]}.{d[3:7]}-{d[7:]}" if len(d) == 11 else str(v or "").strip()

def validate_cpf(cpf: str) -> bool:
    cpf = only_digits(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11: return False
    for i in range(9, 11):
        value = sum((int(cpf[num]) * ((i + 1) - num) for num in range(0, i)))
        digit = ((value * 10) % 11) % 10
        if digit != int(cpf[i]): return False
    return True

# =========================
# Lógica de Dados (Sheets)
# =========================
@st.cache_data(ttl=60)
def load_data():
    """Carrega dados da planilha com cache."""
    client = get_gspread_client()
    if not client: return pd.DataFrame()
    
    ws = open_worksheet(client, SPREADSHEET_ID, WORKSHEET_GID)
    if not ws: return pd.DataFrame()
    
    data = ws.get_all_values()
    if not data:
        ws.append_row(REQUIRED_COLS)
        return pd.DataFrame(columns=REQUIRED_COLS)
    
    df = pd.DataFrame(data[1:], columns=data[0])
    # Garante colunas necessárias
    for col in REQUIRED_COLS:
        if col not in df.columns: df[col] = ""
    
    df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)
    df["_sheet_row"] = df.index + 2
    return df

def get_dropdown_options(df: pd.DataFrame, field: str) -> List[str]:
    """Gera opções para selects baseadas nos dados existentes."""
    existing = df[field].dropna().unique().tolist()
    defaults = {
        "nacionalidade": ["BRASILEIRA", "BRASILEIRO", "OUTRA"],
        "estado_civil": ["SOLTEIRO", "CASADO", "UNIÃO ESTÁVEL", "DIVORCIADO", "VIÚVO"]
    }
    options = sorted(list(set([str(x).strip().upper() for x in existing if x] + defaults.get(field, []))))
    return options if options else ["OUTRO"]

# =========================
# Interface Streamlit
# =========================
def apply_custom_css():
    st.markdown("""
        <style>
        .main { background-color: #f5f7f9; }
        .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #0B3AA8; color: white; }
        .card { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
        .section-header { color: #0B3AA8; font-weight: bold; font-size: 1.2rem; border-bottom: 2px solid #0B3AA8; margin-bottom: 15px; }
        .required-label { color: #d32f2f; font-size: 0.8rem; margin-bottom: 2px; }
        </style>
    """, unsafe_allow_html=True)

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="⛪", layout="centered")
    apply_custom_css()
    
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=150)
    st.title(APP_TITLE)

    df = load_data()
    
    # Inicialização do Estado
    if "step" not in st.session_state: st.session_state.step = "search"
    if "search_results" not in st.session_state: st.session_state.search_results = []

    # --- PASSO 1: BUSCA ---
    if st.session_state.step == "search":
        with st.container():
            st.markdown('<div class="card"><div class="section-header">Identificação do Membro</div>', unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            with col1:
                search_dn = st.date_input("Data de Nascimento", min_value=date(1900, 1, 1), max_value=date.today(), format="DD/MM/YYYY")
            with col2:
                search_mae = st.text_input("Nome da Mãe (Primeiro nome)", placeholder="Ex: Maria")
            
            if st.button("Buscar Cadastro"):
                if not search_mae:
                    st.warning("Por favor, informe o nome da mãe.")
                else:
                    # Lógica de busca
                    mae_token = first_token(search_mae)
                    matches = df[
                        (df["_data_nasc_date"] == search_dn) & 
                        (df["nome_mae"].apply(first_token) == mae_token)
                    ]
                    st.session_state.search_results = matches.index.tolist()
                    st.session_state.search_dn = search_dn
                    st.session_state.search_mae = search_mae
                    st.session_state.step = "edit" if not matches.empty else "new"
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # --- PASSO 2: FORMULÁRIO (NOVO OU EDIÇÃO) ---
    else:
        is_new = st.session_state.step == "new"
        results = st.session_state.search_results
        
        # Seleção se houver múltiplos resultados
        selected_idx = None
        if not is_new:
            if len(results) > 1:
                st.info(f"Encontramos {len(results)} registros. Selecione o correto:")
                options = {idx: f"{df.at[idx, 'nome_completo']} ({df.at[idx, 'congregacao']})" for idx in results}
                selected_idx = st.selectbox("Membro", options.keys(), format_func=lambda x: options[x])
            else:
                selected_idx = results[0]
            
            current_data = df.loc[selected_idx].to_dict()
            st.success(f"Editando cadastro de: **{current_data.get('nome_completo')}**")
        else:
            st.info("Nenhum cadastro encontrado. Preencha os dados para criar um novo.")
            current_data = {col: "" for col in REQUIRED_COLS}
            current_data["data_nasc"] = st.session_state.search_dn
            current_data["nome_mae"] = st.session_state.search_mae

        # Formulário Unificado
        with st.form("member_form"):
            st.markdown('<div class="section-header">Dados Pessoais</div>', unsafe_allow_html=True)
            
            nome = st.text_input("Nome Completo*", value=current_data.get("nome_completo", ""))
            
            c1, c2 = st.columns(2)
            with c1:
                dn = st.date_input("Data de Nascimento*", value=parse_date_any(current_data.get("data_nasc")) or date.today(), format="DD/MM/YYYY")
                cpf = st.text_input("CPF*", value=current_data.get("cpf", ""), placeholder="000.000.000-00")
            with c2:
                whats = st.text_input("WhatsApp/Telefone*", value=current_data.get("whatsapp_telefone", ""), placeholder="(00) 0.0000-0000")
                est_civil = st.selectbox("Estado Civil*", get_dropdown_options(df, "estado_civil"), 
                                       index=get_dropdown_options(df, "estado_civil").index(current_data.get("estado_civil").upper()) if current_data.get("estado_civil") in get_dropdown_options(df, "estado_civil") else 0)

            st.markdown('<div class="section-header">Endereço</div>', unsafe_allow_html=True)
            bairro = st.selectbox("Bairro/Distrito*", BAIRROS_DISTRITOS, 
                                index=BAIRROS_DISTRITOS.index(current_data.get("bairro_distrito")) if current_data.get("bairro_distrito") in BAIRROS_DISTRITOS else 0)
            endereco = st.text_input("Endereço Completo*", value=current_data.get("endereco", ""))

            st.markdown('<div class="section-header">Filiação e Eclesiástico</div>', unsafe_allow_html=True)
            colA, colB = st.columns(2)
            with colA:
                mae = st.text_input("Nome da Mãe*", value=current_data.get("nome_mae", ""))
                nacionalidade = st.selectbox("Nacionalidade", get_dropdown_options(df, "nacionalidade"),
                                           index=get_dropdown_options(df, "nacionalidade").index(current_data.get("nacionalidade").upper()) if current_data.get("nacionalidade") in get_dropdown_options(df, "nacionalidade") else 0)
                congregacao = st.selectbox("Congregação*", get_dropdown_options(df, "congregacao"),
                                         index=get_dropdown_options(df, "congregacao").index(current_data.get("congregacao")) if current_data.get("congregacao") in get_dropdown_options(df, "congregacao") else 0)
            with colB:
                pai = st.text_input("Nome do Pai", value=current_data.get("nome_pai", ""))
                naturalidade = st.text_input("Naturalidade", value=current_data.get("naturalidade", ""))
                batismo = st.text_input("Data do Batismo", value=current_data.get("data_batismo", ""), placeholder="DD/MM/AAAA")

            submitted = st.form_submit_button("Salvar Alterações" if not is_new else "Cadastrar Membro")
            
            if submitted:
                # Validações
                errors = []
                if not nome: errors.append("Nome Completo")
                if not validate_cpf(cpf): errors.append("CPF válido")
                if len(only_digits(whats)) < 10: errors.append("Telefone válido")
                if not mae: errors.append("Nome da Mãe")
                
                if errors:
                    st.error(f"Verifique os campos: {', '.join(errors)}")
                else:
                    # Preparar Payload
                    payload = {
                        "nome_completo": nome.strip().upper(),
                        "data_nasc": dn.strftime("%d/%m/%Y"),
                        "cpf": format_cpf(cpf),
                        "whatsapp_telefone": format_phone_br(whats),
                        "estado_civil": est_civil,
                        "bairro_distrito": bairro,
                        "endereco": endereco.strip().upper(),
                        "nome_mae": mae.strip().upper(),
                        "nome_pai": pai.strip().upper(),
                        "nacionalidade": nacionalidade,
                        "naturalidade": naturalidade.strip().upper(),
                        "congregacao": congregacao,
                        "data_batismo": batismo,
                        "atualizado": datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
                    }
                    
                    client = get_gspread_client()
                    ws = open_worksheet(client, SPREADSHEET_ID, WORKSHEET_GID)
                    
                    try:
                        if is_new:
                            # Gerar novo ID
                            new_id = int(df["membro_id"].replace('', '0').astype(int).max() + 1) if not df.empty else 1
                            payload["membro_id"] = str(new_id)
                            row_to_append = [payload.get(col, "") for col in REQUIRED_COLS]
                            ws.append_row(row_to_append, value_input_option="USER_ENTERED")
                            st.success("Cadastro realizado com sucesso!")
                        else:
                            # Atualizar linha existente
                            sheet_row = int(current_data["_sheet_row"])
                            # Mapeia payload para as colunas da planilha
                            header = ws.row_values(1)
                            row_values = ws.row_values(sheet_row)
                            # Garante tamanho
                            if len(row_values) < len(header): row_values += [""] * (len(header) - len(row_values))
                            
                            for k, v in payload.items():
                                if k in header:
                                    row_values[header.index(k)] = v
                            
                            ws.update(f"A{sheet_row}", [row_values], value_input_option="USER_ENTERED")
                            st.success("Cadastro atualizado com sucesso!")
                        
                        st.cache_data.clear()
                        st.session_state.step = "search"
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao salvar: {e}")

        if st.button("Voltar para Busca"):
            st.session_state.step = "search"
            st.rerun()

if __name__ == "__main__":
    main()
