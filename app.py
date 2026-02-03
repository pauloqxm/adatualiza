import os
import re
import unicodedata
import textwrap
import functools
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple

import pandas as pd
import streamlit as st

# =========================
# Config
# =========================
APP_TITLE = "Atualização de Cadastro da Igreja"
TZ = ZoneInfo("America/Fortaleza")

LOGO_PATH = os.path.join("data", "logo_ad.jpg")
BACKUP_DIR = "backups"

# Google Sheets
SPREADSHEET_ID = "1IUXWrsoBC58-Pe_6mcFQmzgX1xm6GDYvjP1Pd6FH3D0"
WORKSHEET_GID = 1191582738  # id da aba (gid)

REQUIRED_COLS = [
    "membro_id",
    "cod_membro",
    "data_nasc",
    "nome_mae",
    "nome_completo",
    "cpf",
    "whatsapp_telefone",
    "bairro_distrito",
    "endereco",
    "nome_pai",
    "nacionalidade",
    "naturalidade",
    "estado_civil",
    "data_batismo",
    "congregacao",
    "atualizado",
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

# Criar diretório de backups
os.makedirs(BACKUP_DIR, exist_ok=True)

# =========================
# Google auth
# =========================
def get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as e:
        st.error(f"Dependências ausentes: {str(e)}. Instale gspread e google-auth.")
        return None

    creds_info = None

    if "gcp_service_account" in st.secrets:
        creds_info = st.secrets["gcp_service_account"]
    else:
        sa_path = "service_account.json"
        if os.path.exists(sa_path):
            try:
                with open(sa_path, "r", encoding="utf-8") as f:
                    creds_info = json.load(f)
            except Exception as e:
                st.error(f"Erro ao ler service_account.json: {str(e)}")
                return None

        if creds_info is None and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            env_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        creds_info = json.load(f)
                except Exception as e:
                    st.error(f"Erro ao ler credenciais do ambiente: {str(e)}")
                    return None

    if not creds_info:
        st.error("Credenciais não configuradas. Use st.secrets[gcp_service_account] ou service_account.json.")
        return None

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        from google.oauth2.service_account import Credentials
        credentials = Credentials.from_service_account_info(creds_info, scopes=scope)
        import gspread
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"Erro na autenticação: {str(e)}")
        return None


def handle_google_api_errors(func):
    """Decorator para lidar com erros da API do Google."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"Erro na API do Google: {str(e)}"
            if "APIError" in str(type(e).__name__):
                st.error("Erro de comunicação com o Google Sheets. Tente novamente.")
            elif "SpreadsheetNotFound" in str(type(e).__name__):
                st.error("Planilha não encontrada. Verifique o ID.")
            else:
                st.error(error_msg)
            return None
    return wrapper


@handle_google_api_errors
def open_worksheet_by_gid(client, spreadsheet_id: str, gid: int):
    import gspread
    sh = client.open_by_key(spreadsheet_id)
    for ws in sh.worksheets():
        try:
            if int(ws.id) == int(gid):
                return ws
        except Exception:
            continue
    raise gspread.WorksheetNotFound(f"Não achei aba com gid {gid}.")


# =========================
# Helpers
# =========================
def validate_input_text(text: str, max_length: int = 200) -> str:
    """Remove caracteres perigosos e limita tamanho."""
    if not text:
        return ""
    # Remove caracteres de controle
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    # Limita tamanho
    return text[:max_length]


def safe_sheet_value(value):
    """Evita que valores começando com =, +, -, @ executem como fórmulas."""
    if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@'):
        return f"'{value}"
    return value


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    s = validate_input_text(s)
    s = _strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s


def first_token(s) -> str:
    s = norm_text(s)
    if not s:
        return ""
    return s.split(" ", 1)[0]


def only_digits(s) -> str:
    return re.sub(r"\D+", "", str(s or ""))


def clean_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ["nan", "none"]:
        return ""
    return safe_sheet_value(s)


def is_missing(v) -> bool:
    return clean_cell(v) == ""


def parse_date_any(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()

    s = str(v).strip()
    if not s:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue

    try:
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def fmt_date_br(d: date | None) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def format_cpf(cpf_value) -> str:
    d = only_digits(cpf_value)
    if len(d) != 11:
        return str(cpf_value or "").strip()
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def cpf_valido(cpf) -> bool:
    cpf = only_digits(cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False

    def calc_dv(cpf_base: str, pesos: list[int]) -> str:
        soma = sum(int(a) * b for a, b in zip(cpf_base, pesos))
        resto = soma % 11
        dv = 0 if resto < 2 else 11 - resto
        return str(dv)

    base9 = cpf[:9]
    dv1 = calc_dv(base9, list(range(10, 1, -1)))
    base10 = base9 + dv1
    dv2 = calc_dv(base10, list(range(11, 1, -1)))
    return cpf == base9 + dv1 + dv2


def phone_valido(value) -> bool:
    digits = only_digits(value)
    return len(digits) == 11


def format_phone_br(value) -> str:
    d = only_digits(value)
    if len(d) != 11:
        return str(value or "").strip()
    ddd = d[:2]
    n = d[2:]
    return f"({ddd}) {n[0]}.{n[1:5]}-{n[5:]}"


def validate_field_live(field_name: str, value: str) -> Optional[str]:
    """Validação em tempo real com feedback imediato."""
    if not value and field_name in REQUIRED_FIELDS:
        return "Campo obrigatório"
    
    if field_name == "cpf":
        if value and not cpf_valido(only_digits(value)):
            return "CPF inválido"
    
    if field_name == "whatsapp_telefone":
        digits = only_digits(value)
        if digits and len(digits) != 11:
            return "Telefone precisa ter 11 dígitos"
    
    return None


def validate_data_consistency(payload: dict) -> list[str]:
    """Valida regras de negócio adicionais."""
    warnings = []
    
    # Data de batismo não pode ser anterior a 1900
    batismo = parse_date_any(payload.get('data_batismo'))
    if batismo and batismo.year < 1900:
        warnings.append("Data de batismo parece inválida")
    
    # Data de batismo não pode ser futura
    if batismo and batismo > date.today():
        warnings.append("Data de batismo não pode ser no futuro")
    
    # Data de nascimento vs batismo
    nasc = parse_date_any(payload.get('data_nasc'))
    if nasc and batismo and batismo < nasc:
        warnings.append("Data de batismo anterior ao nascimento")
    
    # Idade mínima para batismo (12 anos)
    if nasc and batismo:
        idade_batismo = batismo.year - nasc.year - ((batismo.month, batismo.day) < (nasc.month, nasc.day))
        if idade_batismo < 12:
            warnings.append(f"Idade no batismo ({idade_batismo} anos) é muito baixa")
    
    return warnings


# =========================
# Sheets read/write
# =========================
@st.cache_data(show_spinner=False, ttl=30)
def load_sheet_df(spreadsheet_id: str, gid: int) -> pd.DataFrame:
    client = get_gspread_client()
    if client is None:
        return pd.DataFrame()

    ws = open_worksheet_by_gid(client, spreadsheet_id, gid)
    if ws is None:
        return pd.DataFrame()
    
    values = ws.get_all_values()

    if not values:
        ws.append_row(REQUIRED_COLS[:], value_input_option="USER_ENTERED")
        values = ws.get_all_values()

    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)

    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""

    df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)
    df["_sheet_row"] = df.index + 2
    
    # Adicionar colunas de busca otimizada
    df["_nome_mae_norm"] = df["nome_mae"].apply(norm_text)
    df["_cpf_digits"] = df["cpf"].apply(only_digits)
    
    return df


@st.cache_data(show_spinner=False, ttl=300)
def build_search_index(df: pd.DataFrame) -> Dict:
    """Cria índices para busca mais rápida."""
    if df.empty:
        return {}
    
    return {
        'data_nasc': dict(zip(df.index, df['_data_nasc_date'])),
        'mae_first': dict(zip(df.index, df['nome_mae'].apply(first_token))),
        'mae_norm': dict(zip(df.index, df['_nome_mae_norm'])),
        'cpf_digits': dict(zip(df.index, df['_cpf_digits']))
    }


def ensure_header_columns(ws, df: pd.DataFrame):
    header = ws.row_values(1)
    need_add = [c for c in REQUIRED_COLS if c not in header]
    if need_add:
        ws.update("1:1", [header + need_add])
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""


def build_options_from_df(df: pd.DataFrame, field: str) -> list[str]:
    if df.empty:
        series = pd.Series([], dtype=str)
    else:
        series = df.get(field, pd.Series([], dtype=str)).fillna("").astype(str).map(lambda x: x.strip())
    
    vals = [x for x in series.tolist() if x and x.lower() != "nan"]
    uniq = sorted(set(vals), key=lambda x: x.casefold())

    # defaults para não ficar vazio
    if field == "nacionalidade":
        defaults = ["BRASILEIRA", "BRASILEIRO", "OUTRA"]
        for d in defaults:
            if d not in uniq:
                uniq.append(d)

    if field == "estado_civil":
        defaults = ["SOLTEIRO", "CASADO", "UNIÃO ESTÁVEL", "DIVORCIADO", "VIÚVO", "OUTRO"]
        for d in defaults:
            if d not in uniq:
                uniq.append(d)

    if not uniq:
        uniq = ["OUTRO"]

    return uniq


def find_matches(df: pd.DataFrame, dn: date, mae: str) -> pd.DataFrame:
    mae_first = first_token(mae)
    if not mae_first:
        return df.iloc[0:0].copy()
    mask_dn = df["_data_nasc_date"] == dn
    mask_mae = df["nome_mae"].apply(lambda x: first_token(x) == mae_first)
    return df[mask_dn & mask_mae].copy()


def find_matches_multiple(df: pd.DataFrame, search_criteria: dict) -> pd.DataFrame:
    """Busca flexível por múltiplos critérios."""
    if df.empty:
        return df.iloc[0:0].copy()
    
    masks = []
    
    if search_criteria.get('data_nasc'):
        masks.append(df["_data_nasc_date"] == search_criteria['data_nasc'])
    
    if search_criteria.get('nome_mae'):
        mae_first = first_token(search_criteria['nome_mae'])
        masks.append(df["nome_mae"].apply(lambda x: first_token(x) == mae_first))
    
    if search_criteria.get('cpf'):
        cpf_digits = only_digits(search_criteria['cpf'])
        masks.append(df["cpf"].apply(lambda x: only_digits(x) == cpf_digits))
    
    if search_criteria.get('nome'):
        nome_norm = norm_text(search_criteria['nome'])
        masks.append(df["nome_completo"].apply(lambda x: nome_norm in norm_text(x)))
    
    if not masks:
        return df.iloc[0:0].copy()
    
    # Combina todas as condições com AND
    combined_mask = pd.Series(True, index=df.index)
    for mask in masks:
        combined_mask &= mask
    
    return df[combined_mask].copy()


def gspread_a1(r1, c1, r2, c2):
    def col_to_a1(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s
    return f"{col_to_a1(c1)}{r1}:{col_to_a1(c2)}{r2}"


def backup_before_update(ws, sheet_row: int, backup_name: str = "") -> str:
    """Cria backup da linha antes de atualizar."""
    try:
        row_data = ws.row_values(sheet_row)
        
        backup_file = os.path.join(
            BACKUP_DIR, 
            f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sheet_row}_{backup_name}.json"
        )
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'sheet_row': sheet_row,
                'spreadsheet_id': SPREADSHEET_ID,
                'worksheet_gid': WORKSHEET_GID,
                'data': row_data
            }, f, ensure_ascii=False, indent=2)
        
        return backup_file
    except Exception as e:
        st.warning(f"Não foi possível criar backup: {str(e)}")
        return ""


@handle_google_api_errors
def update_row_in_sheet(ws, sheet_row: int, header: list[str], payload: dict):
    # Criar backup antes da atualização
    member_name = payload.get('nome_completo', 'unknown')[:20]
    backup_file = backup_before_update(ws, sheet_row, member_name)
    
    row_values = ws.row_values(sheet_row)
    if len(row_values) < len(header):
        row_values = row_values + [""] * (len(header) - len(row_values))

    for k, v in payload.items():
        if k in header:
            idx = header.index(k)
            row_values[idx] = clean_cell(v)

    ws.update(
        range_name=gspread_a1(sheet_row, 1, sheet_row, len(header)),
        values=[row_values],
        value_input_option="USER_ENTERED"
    )
    
    if backup_file:
        st.info(f"Backup salvo: {os.path.basename(backup_file)}")


@handle_google_api_errors
def append_row_in_sheet(ws, header: list[str], payload: dict):
    row = [clean_cell(payload.get(c, "")) for c in header]
    ws.append_row(row, value_input_option="USER_ENTERED")


def next_membro_id(df: pd.DataFrame) -> int:
    if df.empty:
        return 1
    
    s = df.get("membro_id", pd.Series([], dtype=str)).fillna("").astype(str).map(lambda x: only_digits(x))
    nums = pd.to_numeric(s, errors="coerce")
    if nums.notna().any():
        return int(nums.max()) + 1
    return 1


def validate_required(payload: dict) -> list[str]:
    missing = []
    for k, label in REQUIRED_FIELDS.items():
        v = payload.get(k, "")
        if k == "data_nasc":
            if not v:
                missing.append(label)
        else:
            if clean_cell(v) == "":
                missing.append(label)
    return missing


def check_connection() -> bool:
    """Verifica se há conexão com o Google Sheets."""
    try:
        client = get_gspread_client()
        if client is None:
            return False
        ws = open_worksheet_by_gid(client, SPREADSHEET_ID, WORKSHEET_GID)
        if ws is None:
            return False
        ws.row_values(1)
        return True
    except Exception:
        return False


# =========================
# UI setup
# =========================
st.set_page_config(page_title="Igreja - Atualização de Cadastro", page_icon="📘", layout="centered")

# CSS personalizado com modo claro/escuro
st.markdown(
    """
<style>
:root{
  --blue:#1D4ED8;
  --blue2:#0B3AA8;
  --muted:#475569;
  --border:#DBEAFE;
  --shadow: 0 10px 20px rgba(2, 6, 23, .08);
  --danger:#DC2626;
  --dangerSoft:#FEE2E2;
  --success:#10B981;
  --successSoft:#D1FAE5;
  --warning:#F59E0B;
  --warningSoft:#FEF3C7;
}

[data-theme="dark"] {
  --blue:#3B82F6;
  --blue2:#1D4ED8;
  --muted:#94A3B8;
  --border:#1E293B;
  --shadow: 0 10px 20px rgba(0, 0, 0, 0.3);
  --danger:#DC2626;
  --dangerSoft:#450A0A;
  --success:#10B981;
  --successSoft:#022C22;
  --warning:#F59E0B;
  --warningSoft:#451A03;
}

.main, .stApp{
  background: linear-gradient(135deg, #EFF6FF 0%, #FFFFFF 55%, #E0F2FE 100%);
}

[data-theme="dark"] .main, [data-theme="dark"] .stApp {
  background: linear-gradient(135deg, #0F172A 0%, #1E293B 55%, #334155 100%);
}

.topbar{
  background: linear-gradient(135deg, var(--blue), var(--blue2));
  color: white;
  border-radius: 18px;
  padding: 16px 18px;
  box-shadow: var(--shadow);
  margin-bottom: 18px;
  display:flex;
  align-items:center;
  gap:12px;
  transition: all 0.3s ease;
}

.topbar-title{
  display:flex;
  flex-direction:column;
  gap:2px;
}

.topbar-title h1{
  margin:0;
  font-size: 1.25rem;
  font-weight: 900;
  line-height: 1.1;
}

.topbar-title p{
  margin:0;
  opacity:.95;
  font-weight: 650;
}

.card{
  background: var(--card-bg, white);
  border: 2px solid var(--border);
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
  margin: 14px 0;
  transition: all 0.3s ease;
}

[data-theme="dark"] .card {
  --card-bg: #1E293B;
  color: #E2E8F0;
}

.section{ 
  font-weight: 900; 
  color: var(--blue2); 
  font-size: 1.15rem; 
  margin-bottom: 10px; 
}

[data-theme="dark"] .section {
  color: #60A5FA;
}

.small{ 
  color: var(--muted); 
  font-weight: 650; 
}

div.stButton>button{
  background: linear-gradient(135deg, var(--blue), var(--blue2));
  color:#fff;
  border:none;
  border-radius: 14px;
  padding: 12px 18px;
  font-weight: 900;
  font-size: 1.05rem;
  width:100%;
  box-shadow: var(--shadow);
  transition: all 0.3s ease;
  cursor: pointer;
}

div.stButton>button:hover{
  transform: translateY(-2px);
  box-shadow: 0 15px 30px rgba(2, 6, 23, .15);
}

.stTextInput input, .stSelectbox select, .stDateInput input{
  border-radius: 12px !important;
  border: 2px solid var(--border) !important;
  padding: 12px !important;
  font-size: 1rem !important;
  transition: all 0.3s ease;
}

.stTextInput input:focus, .stSelectbox select:focus, .stDateInput input:focus{
  border-color: var(--blue) !important;
  box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.1) !important;
}

hr{
  border: none;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--border), transparent);
  margin: 18px 0;
}

.miss-wrap{
  border: 2px solid var(--danger);
  background: linear-gradient(135deg, var(--dangerSoft), var(--card-bg, white));
  border-radius: 16px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.miss-label{
  color: var(--danger);
  font-weight: 900;
  margin: 0;
}

.found-name{
  margin-top:10px;
  font-weight:900;
  color: var(--blue2);
  font-size: 1.15rem;
}

[data-theme="dark"] .found-name {
  color: #93C5FD;
}

.cong-muted{
  margin-top: 6px;
  font-size: .92rem;
  font-weight: 700;
  color: var(--muted);
}

.validation-error{
  color: var(--danger);
  font-size: 0.9rem;
  font-weight: 600;
  margin-top: 4px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--dangerSoft);
}

.validation-warning{
  color: var(--warning);
  font-size: 0.9rem;
  font-weight: 600;
  margin-top: 4px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--warningSoft);
}

.validation-success{
  color: var(--success);
  font-size: 0.9rem;
  font-weight: 600;
  margin-top: 4px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--successSoft);
}

.connection-status{
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}

.connection-online{
  background-color: var(--success);
  box-shadow: 0 0 10px var(--success);
}

.connection-offline{
  background-color: var(--danger);
  box-shadow: 0 0 10px var(--danger);
}

.field-tooltip{
  font-size: 0.8rem;
  color: var(--muted);
  margin-top: 4px;
}

.loading-overlay{
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.8);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 9999;
}

[data-theme="dark"] .loading-overlay {
  background: rgba(0, 0, 0, 0.8);
}
</style>
""",
    unsafe_allow_html=True,
)

# Logo pequena dentro do retângulo
logo_html = ""
if os.path.exists(LOGO_PATH):
    import base64
    try:
        with open(LOGO_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        logo_html = (
            f"<img src='data:image/jpeg;base64,{b64}' "
            "style='width:56px;height:56px;object-fit:contain;border-radius:12px;"
            "background:rgba(255,255,255,.15);padding:6px;' />"
        )
    except Exception:
        pass

# Status de conexão
connection_status = check_connection()
status_html = f"""
<span class='connection-status {'connection-online' if connection_status else 'connection-offline'}'></span>
{'Conectado' if connection_status else 'Sem conexão'}
"""

st.markdown(
    f"""
<div class="topbar">
  {logo_html}
  <div class="topbar-title">
    <h1>{APP_TITLE}</h1>
    <p>Digite data de nascimento e o primeiro nome da mãe para encontrar seu cadastro.</p>
    <div style="font-size: 0.8rem; opacity: 0.8; margin-top: 4px;">
      {status_html}
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# Sidebar com informações
with st.sidebar:
    st.markdown("### 📊 Estatísticas")
    
    with st.spinner("Carregando dados..."):
        df = load_sheet_df(SPREADSHEET_ID, WORKSHEET_GID)
    
    if not df.empty:
        total_membros = len(df)
        atualizados = df['atualizado'].apply(lambda x: bool(str(x).strip())).sum()
        percentual = (atualizados / total_membros * 100) if total_membros > 0 else 0
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total de Membros", total_membros)
        with col2:
            st.metric("Atualizados", f"{percentual:.1f}%")
        
        st.progress(percentual / 100)
        
        st.markdown("---")
        st.markdown("### 🔍 Busca Avançada")
        
        search_type = st.radio("Tipo de busca:", ["Data + Nome da Mãe", "CPF", "Nome"])
        
        if search_type == "CPF":
            cpf_search = st.text_input("Digite o CPF:", placeholder="000.000.000-00")
            if st.button("Buscar por CPF", use_container_width=True):
                if cpf_search:
                    matches = find_matches_multiple(df, {'cpf': cpf_search})
                    st.session_state.searched = True
                    st.session_state.match_ids = matches.index.tolist()
                    st.session_state.search_type = "cpf"
        
        elif search_type == "Nome":
            nome_search = st.text_input("Digite parte do nome:")
            if st.button("Buscar por Nome", use_container_width=True):
                if nome_search:
                    matches = find_matches_multiple(df, {'nome': nome_search})
                    st.session_state.searched = True
                    st.session_state.match_ids = matches.index.tolist()
                    st.session_state.search_type = "nome"
        
        st.markdown("---")
        st.markdown("### ⚙️ Configurações")
        
        # Botão para limpar cache
        if st.button("🔄 Atualizar Cache", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        
        # Botão para limpar formulário
        if st.button("🧹 Limpar Busca", use_container_width=True):
            st.session_state.searched = False
            st.session_state.match_ids = []
            st.session_state.search_dn = None
            st.session_state.search_mae = ""
            st.rerun()

if df.empty:
    st.error("Não foi possível carregar os dados. Verifique a conexão e tente novamente.")
    st.stop()

dropdown_opts = {f: build_options_from_df(df, f) for f in DROPDOWN_FIELDS}

# Estado
if "searched" not in st.session_state:
    st.session_state.searched = False
if "match_ids" not in st.session_state:
    st.session_state.match_ids = []
if "search_dn" not in st.session_state:
    st.session_state.search_dn = None
if "search_mae" not in st.session_state:
    st.session_state.search_mae = ""
if "search_type" not in st.session_state:
    st.session_state.search_type = "default"
if "form_draft" not in st.session_state:
    st.session_state.form_draft = {}

# Índice de busca otimizada
search_index = build_search_index(df)

def field_block(label: str, field_name: str, current_value="", *, 
                render_fn, is_required: bool = True, tooltip: str = ""):
    """Renderiza um campo com validação em tempo real."""
    col1, col2 = st.columns([3, 1])
    
    with col1:
        # Validação em tempo real
        validation_msg = None
        if field_name in st.session_state.form_draft:
            validation_msg = validate_field_live(field_name, st.session_state.form_draft[field_name])
        elif current_value:
            validation_msg = validate_field_live(field_name, current_value)
        
        if is_required and (not current_value or validation_msg):
            st.markdown(
                f"<div class='miss-wrap'><p class='miss-label'>{label} obrigatório</p></div>",
                unsafe_allow_html=True,
            )
        
        # Campo de entrada
        result = render_fn()
        
        # Salvar no rascunho
        if result is not None and result != current_value:
            st.session_state.form_draft[field_name] = result
    
    with col2:
        # Ícone de validação
        if validation_msg:
            st.markdown(f"<div class='validation-error'>⚠️</div>", unsafe_allow_html=True)
        elif current_value and not validation_msg:
            st.markdown(f"<div class='validation-success'>✓</div>", unsafe_allow_html=True)
    
    # Mensagem de validação
    if validation_msg:
        st.markdown(f"<div class='validation-error'>{validation_msg}</div>", unsafe_allow_html=True)
    
    # Tooltip
    if tooltip:
        st.markdown(f"<div class='field-tooltip'>{tooltip}</div>", unsafe_allow_html=True)
    
    return result


def dropdown_only(label, field_name, current_value="", key_prefix="x"):
    opts = dropdown_opts.get(field_name, []) or ["OUTRO"]
    cur = str(current_value or "").strip()
    idx = opts.index(cur) if cur in opts else 0
    return st.selectbox(
        label,
        options=opts,
        index=idx,
        key=f"{key_prefix}_{field_name}_sel",
    )


def render_found_card(d: dict, total: int):
    dn = parse_date_any(d.get("data_nasc", "")) or st.session_state.search_dn
    mae = clean_cell(d.get("nome_mae", "")) or st.session_state.search_mae
    nome = clean_cell(d.get("nome_completo", "")) or "(Sem nome)"
    cong = clean_cell(d.get("congregacao", ""))
    cpf = clean_cell(d.get("cpf", ""))
    
    last_update = d.get("atualizado", "")
    update_info = f"<br><small>Última atualização: {last_update}</small>" if last_update else ""

    html = f"""
    <div class="card">
      <div class="section">Cadastro encontrado</div>
      <div class="small">Achamos {total} registro(s). Selecione e atualize.</div>

      <div style="margin-top:12px;">
        <div class="small"><b>Data de nascimento</b></div>
        <div style="font-weight:800;color:var(--blue2);font-size:1.05rem;margin-bottom:10px;">
          {fmt_date_br(dn) if dn else ""}
        </div>

        <div class="small"><b>Nome da mãe</b></div>
        <div style="font-weight:800;color:var(--blue2);font-size:1.05rem;margin-bottom:12px;">
          {mae}
        </div>

        <div class="small"><b>Nome</b></div>
        <div class="found-name">{nome}</div>
        <div class="small"><b>CPF</b></div>
        <div style="margin-bottom:8px;">{format_cpf(cpf) if cpf else "Não informado"}</div>
        <div class="cong-muted">Congregação: {cong if cong else "sem informação"}{update_info}</div>
      </div>
    </div>
    """
    st.markdown(textwrap.dedent(html).strip(), unsafe_allow_html=True)


# =========================
# Busca
# =========================
st.markdown('<div class="card"><div class="section">Identificação do membro</div></div>', unsafe_allow_html=True)

search_tabs = st.tabs(["Busca Padrão", "Busca por CPF", "Busca por Nome"])

with search_tabs[0]:
    col1, col2 = st.columns(2)
    with col1:
        inp_dn = st.date_input(
            "Data de nascimento",
            value=st.session_state.search_dn,
            min_value=date(1900, 1, 1),
            max_value=date.today(),
            format="DD/MM/YYYY",
        )
    with col2:
        inp_mae = st.text_input(
            "Nome da mãe",
            value=st.session_state.search_mae,
            placeholder="Ex.: Maria",
            help="Digite pelo menos o primeiro nome"
        )

    if st.button("🔍 Buscar cadastro", use_container_width=True):
        if inp_dn is None:
            st.warning("Escolha a data de nascimento.")
        elif not inp_mae.strip():
            st.warning("Digite o nome da mãe.")
        else:
            with st.spinner("Buscando..."):
                matches = find_matches(df, inp_dn, inp_mae)
                st.session_state.searched = True
                st.session_state.search_dn = inp_dn
                st.session_state.search_mae = inp_mae.strip()
                st.session_state.match_ids = matches.index.tolist()
                st.session_state.search_type = "default"

with search_tabs[1]:
    cpf_search = st.text_input("CPF para busca:", placeholder="000.000.000-00", key="cpf_search_tab")
    if st.button("🔍 Buscar por CPF", key="search_cpf_tab", use_container_width=True):
        if cpf_search:
            with st.spinner("Buscando por CPF..."):
                matches = find_matches_multiple(df, {'cpf': cpf_search})
                st.session_state.searched = True
                st.session_state.match_ids = matches.index.tolist()
                st.session_state.search_type = "cpf"
                st.rerun()

with search_tabs[2]:
    nome_search = st.text_input("Nome para busca:", placeholder="Digite parte do nome", key="nome_search_tab")
    if st.button("🔍 Buscar por Nome", key="search_nome_tab", use_container_width=True):
        if nome_search:
            with st.spinner("Buscando por nome..."):
                matches = find_matches_multiple(df, {'nome': nome_search})
                st.session_state.searched = True
                st.session_state.match_ids = matches.index.tolist()
                st.session_state.search_type = "nome"
                st.rerun()

st.divider()

if not st.session_state.searched:
    st.info("👆 Use a busca acima para encontrar um cadastro ou criar um novo.")
    st.stop()

match_ids = st.session_state.match_ids

client = get_gspread_client()
if client is None:
    st.error("Não foi possível conectar ao Google Sheets.")
    st.stop()

ws = open_worksheet_by_gid(client, SPREADSHEET_ID, WORKSHEET_GID)
if ws is None:
    st.error("Não foi possível abrir a planilha.")
    st.stop()

ensure_header_columns(ws, df)
header = ws.row_values(1)

# =========================
# Novo cadastro
# =========================
if len(match_ids) == 0:
    st.markdown(
        """
<div class="card">
  <div class="section">Novo cadastro</div>
  <div class="small">Não encontramos registro. Preencha os dados abaixo.</div>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.form("novo_cadastro"):
        dn_val = st.session_state.search_dn
        mae_val = st.session_state.search_mae

        st.markdown("### 📝 Dados Pessoais")
        
        # Campos obrigatórios
        nome_completo = field_block(
            "Nome completo", "nome_completo", "",
            render_fn=lambda: st.text_input("Nome completo", value="", key="new_nome"),
            tooltip="Digite o nome completo sem abreviações"
        )
        
        data_nasc = field_block(
            "Data de nascimento", "data_nasc", dn_val,
            render_fn=lambda: st.date_input(
                "Data de nascimento",
                value=dn_val,
                min_value=date(1900, 1, 1),
                max_value=date.today(),
                format="DD/MM/YYYY",
                key="new_dn"
            ),
            tooltip="Data de nascimento do membro"
        )
        
        cpf_raw = field_block(
            "CPF", "cpf", "",
            render_fn=lambda: st.text_input("CPF", value="", placeholder="000.000.000-00", key="new_cpf"),
            tooltip="CPF válido com 11 dígitos"
        )
        
        whatsapp_raw = field_block(
            "WhatsApp/Telefone", "whatsapp_telefone", "",
            render_fn=lambda: st.text_input("WhatsApp/Telefone", value="", placeholder="(88) 9.9999-9999", key="new_whats"),
            tooltip="Número com DDD e 9 dígitos"
        )
        
        bairro = field_block(
            "Bairro/Distrito", "bairro_distrito", "",
            render_fn=lambda: st.selectbox("Bairro/Distrito", options=BAIRROS_DISTRITOS, index=0, key="new_bairro"),
            tooltip="Selecione o bairro ou distrito"
        )
        
        endereco = field_block(
            "Endereço", "endereco", "",
            render_fn=lambda: st.text_input("Endereço", value="", key="new_endereco"),
            tooltip="Rua, número, complemento"
        )
        
        st.markdown("### 👨‍👩‍👧‍👦 Dados Familiares")
        colA, colB = st.columns(2)
        with colA:
            nome_mae = field_block(
                "Nome da mãe", "nome_mae", mae_val,
                render_fn=lambda: st.text_input("Nome da mãe", value=mae_val, key="new_mae"),
                tooltip="Nome completo da mãe"
            )
        with colB:
            nome_pai = st.text_input("Nome do pai", value="", key="new_pai")
        
        st.markdown("### 📋 Outras Informações")
        
        naturalidade = st.text_input("Naturalidade", value="", key="new_naturalidade",
                                    help="Cidade/Estado de nascimento")
        
        nacionalidade = dropdown_only("Nacionalidade", "nacionalidade", "", key_prefix="new")
        
        estado_civil = field_block(
            "Estado civil", "estado_civil", "",
            render_fn=lambda: dropdown_only("Estado civil", "estado_civil", "", key_prefix="new"),
            tooltip="Estado civil atual"
        )
        
        data_batismo = st.text_input("Data do batismo", value="", placeholder="Ex.: 05/12/1992", 
                                    key="new_batismo", help="Formato DD/MM/AAAA")
        
        congregacao = field_block(
            "Congregação", "congregacao", "",
            render_fn=lambda: dropdown_only("Congregação", "congregacao", "", key_prefix="new"),
            tooltip="Congregação que frequenta"
        )
        
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            salvar = st.form_submit_button("💾 Salvar novo cadastro", use_container_width=True)
        with col2:
            limpar = st.form_submit_button("🗑️ Limpar formulário", type="secondary", use_container_width=True)
            if limpar:
                st.session_state.form_draft = {}
                st.rerun()

        if salvar:
            dn_form = st.session_state.get("new_dn")
            mae_form = st.session_state.get("new_mae", "").strip()

            cpf_digits = only_digits(cpf_raw)
            phone_digits = only_digits(whatsapp_raw)

            now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
            novo_id = next_membro_id(df)

            payload = {
                "membro_id": str(novo_id),
                "cod_membro": "",
                "data_nasc": fmt_date_br(dn_form) if dn_form else "",
                "nome_mae": mae_form,
                "nome_completo": nome_completo.strip(),
                "cpf": format_cpf(cpf_digits),
                "whatsapp_telefone": format_phone_br(phone_digits),
                "bairro_distrito": bairro,
                "endereco": endereco.strip(),
                "nome_pai": nome_pai.strip(),
                "nacionalidade": nacionalidade,
                "naturalidade": naturalidade.strip(),
                "estado_civil": estado_civil,
                "data_batismo": data_batismo.strip(),
                "congregacao": congregacao,
                "atualizado": now_str,
            }

            # Validação completa
            missing_list = validate_required(payload)
            if missing_list:
                st.error("❌ Preencha os campos obrigatórios: " + ", ".join(missing_list) + ".")
                st.stop()

            if not cpf_valido(cpf_digits):
                st.error("❌ CPF inválido. Confira e tente de novo.")
                st.stop()

            if not phone_valido(phone_digits):
                st.error("❌ WhatsApp inválido. Precisa ter 11 números (DDD + 9 dígitos).")
                st.stop()

            # Validações adicionais
            warnings = validate_data_consistency(payload)
            if warnings:
                for warning in warnings:
                    st.warning(f"⚠️ {warning}")
                
                proceed = st.checkbox("Deseja prosseguir mesmo assim?")
                if not proceed:
                    st.stop()

            try:
                with st.spinner("Salvando..."):
                    append_row_in_sheet(ws, header, payload)
                    st.success("✅ Cadastro salvo com sucesso!")
                    
                    # Limpar cache e estado
                    st.cache_data.clear()
                    st.session_state.form_draft = {}
                    st.session_state.searched = False
                    st.session_state.match_ids = []
                    st.session_state.search_dn = None
                    st.session_state.search_mae = ""
                    
                    st.balloons()
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Erro ao salvar: {str(e)}")

    st.stop()

# =========================
# Editar cadastro
# =========================
matches_df = df.loc[match_ids].copy()
total_found = len(matches_df)

if total_found > 1:
    matches_df = matches_df.sort_values(by=["nome_completo"], na_position="last")
    options = []
    for idx, r in matches_df.iterrows():
        nome = clean_cell(r.get("nome_completo", "")) or "(Sem nome)"
        cong = clean_cell(r.get("congregacao", ""))
        cpf = clean_cell(r.get("cpf", ""))
        display_text = f"{nome}"
        if cpf:
            display_text += f" | CPF: {format_cpf(cpf)}"
        if cong:
            display_text += f" | Cong: {cong}"
        options.append((idx, display_text))

    sel = st.selectbox("Selecione o membro", options=options, format_func=lambda x: x[1])
    sel_idx = sel[0]
else:
    sel_idx = matches_df.index[0]

row_preview = df.loc[sel_idx].to_dict()
render_found_card(row_preview, total_found)

row = df.loc[sel_idx].copy()
sheet_row = int(row["_sheet_row"])

bairro_current = clean_cell(row.get("bairro_distrito", ""))
bairro_index = BAIRROS_DISTRITOS.index(bairro_current) if bairro_current in BAIRROS_DISTRITOS else 0
row_dn = parse_date_any(row.get("data_nasc", ""))

with st.form("editar_cadastro"):
    st.markdown("### ✏️ Editar Cadastro")
    
    # Dados pessoais
    nome_completo = field_block(
        "Nome completo", "nome_completo", clean_cell(row.get("nome_completo", "")),
        render_fn=lambda: st.text_input("Nome completo", 
                                       value=clean_cell(row.get("nome_completo", "")), 
                                       key="edit_nome"),
        tooltip="Nome completo sem abreviações"
    )
    
    data_nasc = field_block(
        "Data de nascimento", "data_nasc", row_dn,
        render_fn=lambda: st.date_input(
            "Data de nascimento",
            value=row_dn,
            min_value=date(1900, 1, 1),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="edit_dn"
        ),
        tooltip="Data de nascimento"
    )
    
    cpf_raw = field_block(
        "CPF", "cpf", format_cpf(row.get("cpf", "")),
        render_fn=lambda: st.text_input("CPF", 
                                       value=format_cpf(row.get("cpf", "")), 
                                       placeholder="000.000.000-00", 
                                       key="edit_cpf"),
        tooltip="CPF válido com 11 dígitos"
    )
    
    whatsapp_raw = field_block(
        "WhatsApp/Telefone", "whatsapp_telefone", format_phone_br(row.get("whatsapp_telefone", "")),
        render_fn=lambda: st.text_input("WhatsApp/Telefone", 
                                       value=format_phone_br(row.get("whatsapp_telefone", "")), 
                                       placeholder="(88) 9.9999-9999", 
                                       key="edit_whats"),
        tooltip="Número com DDD e 9 dígitos"
    )
    
    bairro = field_block(
        "Bairro/Distrito", "bairro_distrito", bairro_current,
        render_fn=lambda: st.selectbox("Bairro/Distrito", 
                                      options=BAIRROS_DISTRITOS, 
                                      index=bairro_index, 
                                      key="edit_bairro"),
        tooltip="Selecione o bairro ou distrito"
    )
    
    endereco = field_block(
        "Endereço", "endereco", clean_cell(row.get("endereco", "")),
        render_fn=lambda: st.text_input("Endereço", 
                                       value=clean_cell(row.get("endereco", "")), 
                                       key="edit_endereco"),
        tooltip="Rua, número, complemento"
    )
    
    st.markdown("### 👨‍👩‍👧‍👦 Dados Familiares")
    colA, colB = st.columns(2)
    with colA:
        nome_mae = field_block(
            "Nome da mãe", "nome_mae", clean_cell(row.get("nome_mae", "")),
            render_fn=lambda: st.text_input("Nome da mãe", 
                                           value=clean_cell(row.get("nome_mae", "")), 
                                           key="edit_mae"),
            tooltip="Nome completo da mãe"
        )
    with colB:
        nome_pai = st.text_input("Nome do pai", 
                                value=clean_cell(row.get("nome_pai", "")), 
                                key="edit_pai")
    
    st.markdown("### 📋 Outras Informações")
    
    naturalidade = st.text_input("Naturalidade", 
                                value=clean_cell(row.get("naturalidade", "")), 
                                key="edit_naturalidade")
    
    nacionalidade = dropdown_only("Nacionalidade", "nacionalidade", 
                                 row.get("nacionalidade", ""), key_prefix="edit")
    
    estado_civil = field_block(
        "Estado civil", "estado_civil", row.get("estado_civil", ""),
        render_fn=lambda: dropdown_only("Estado civil", "estado_civil", 
                                       row.get("estado_civil", ""), key_prefix="edit"),
        tooltip="Estado civil atual"
    )
    
    data_batismo = st.text_input("Data do batismo", 
                                value=clean_cell(row.get("data_batismo", "")), 
                                key="edit_batismo",
                                placeholder="DD/MM/AAAA")
    
    congregacao = field_block(
        "Congregação", "congregacao", row.get("congregacao", ""),
        render_fn=lambda: dropdown_only("Congregação", "congregacao", 
                                       row.get("congregacao", ""), key_prefix="edit"),
        tooltip="Congregação que frequenta"
    )
    
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        salvar = st.form_submit_button("💾 Salvar atualização", use_container_width=True)
    with col2:
        cancelar = st.form_submit_button("❌ Cancelar", type="secondary", use_container_width=True)
        if cancelar:
            st.session_state.form_draft = {}
            st.rerun()
    with col3:
        excluir = st.form_submit_button("🗑️ Excluir cadastro", type="secondary", use_container_width=True)

    if salvar:
        nome_form = st.session_state.get("edit_nome", "").strip()
        dn_form = st.session_state.get("edit_dn")
        cpf_form = st.session_state.get("edit_cpf", "")
        whats_form = st.session_state.get("edit_whats", "")
        bairro_form = st.session_state.get("edit_bairro", "")
        endereco_form = st.session_state.get("edit_endereco", "").strip()
        mae_form = st.session_state.get("edit_mae", "").strip()

        cpf_digits = only_digits(cpf_form)
        phone_digits = only_digits(whats_form)
        now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

        payload = {
            "nome_completo": nome_form,
            "data_nasc": fmt_date_br(dn_form) if dn_form else "",
            "cpf": format_cpf(cpf_digits),
            "whatsapp_telefone": format_phone_br(phone_digits),
            "bairro_distrito": bairro_form,
            "endereco": endereco_form,
            "nome_mae": mae_form,
            "nome_pai": nome_pai.strip(),
            "nacionalidade": nacionalidade,
            "naturalidade": naturalidade.strip(),
            "estado_civil": estado_civil,
            "data_batismo": data_batismo.strip(),
            "congregacao": congregacao,
            "atualizado": now_str,
        }

        # Validação completa
        missing_list = validate_required(payload)
        if missing_list:
            st.error("❌ Preencha os campos obrigatórios: " + ", ".join(missing_list) + ".")
            st.stop()

        if not cpf_valido(cpf_digits):
            st.error("❌ CPF inválido. Confira e tente de novo.")
            st.stop()

        if not phone_valido(phone_digits):
            st.error("❌ WhatsApp inválido. Precisa ter 11 números (DDD + 9 dígitos).")
            st.stop()

        # Validações adicionais
        warnings = validate_data_consistency(payload)
        if warnings:
            for warning in warnings:
                st.warning(f"⚠️ {warning}")
            
            proceed = st.checkbox("Deseja prosseguir mesmo assim?")
            if not proceed:
                st.stop()

        try:
            with st.spinner("Atualizando..."):
                update_row_in_sheet(ws, sheet_row, header, payload)
                st.success("✅ Cadastro atualizado com sucesso!")
                
                # Limpar cache e estado
                st.cache_data.clear()
                st.session_state.form_draft = {}
                st.session_state.searched = False
                st.session_state.match_ids = []
                st.session_state.search_dn = None
                st.session_state.search_mae = ""
                
                st.balloons()
                st.rerun()
        except Exception as e:
            st.error(f"❌ Erro ao atualizar: {str(e)}")
    
    elif excluir:
        st.warning("⚠️ Funcionalidade de exclusão não implementada por segurança.")
        st.info("Para excluir um cadastro, contate o administrador do sistema.")

# =========================
# Footer
# =========================
st.markdown("---")
st.markdown(
    """
<div style="text-align: center; color: var(--muted); font-size: 0.9rem;">
    <p>© 2024 Sistema de Cadastro da Igreja | Desenvolvido com Streamlit</p>
    <p style="font-size: 0.8rem;">
        Dados atualizados em tempo real | 
        <span id="last-update">{}</span>
    </p>
</div>
<script>
    function updateTime() {
        const now = new Date();
        const options = { 
            timeZone: 'America/Fortaleza',
            hour12: false,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        };
        document.getElementById('last-update').textContent = 
            now.toLocaleString('pt-BR', options);
    }
    updateTime();
    setInterval(updateTime, 1000);
</script>
""".format(datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")),
    unsafe_allow_html=True
)
