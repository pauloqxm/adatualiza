import os
import re
import unicodedata
import textwrap
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# =========================
# Config
# =========================
APP_TITLE = "Atualização de Cadastro da Igreja"
TZ = ZoneInfo("America/Fortaleza")

LOGO_PATH = os.path.join("data", "logo_ad.jpg")

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


# =========================
# Google auth
# =========================
@st.cache_resource
def get_gspread_client():
    """Cache do cliente gspread para evitar reconexões"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception:
        st.error("Dependências ausentes. Instale gspread e google-auth no requirements.txt.")
        return None

    creds_info = None

    # Prioridade: st.secrets > service_account.json > env var
    if "gcp_service_account" in st.secrets:
        creds_info = st.secrets["gcp_service_account"]
    else:
        sa_path = "service_account.json"
        if os.path.exists(sa_path):
            import json
            with open(sa_path, "r", encoding="utf-8") as f:
                creds_info = json.load(f)
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            import json
            env_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    creds_info = json.load(f)

    if not creds_info:
        st.error("Credenciais não configuradas. Use st.secrets[gcp_service_account] ou service_account.json.")
        return None

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(creds_info, scopes=scope)
    return gspread.authorize(credentials)


def open_worksheet_by_gid(client, spreadsheet_id: str, gid: int):
    """Abre planilha pelo GID"""
    import gspread
    sh = client.open_by_key(spreadsheet_id)
    for ws in sh.worksheets():
        try:
            if int(ws.id) == int(gid):
                return ws
        except Exception:
            pass
    raise gspread.WorksheetNotFound(f"Não achei aba com gid {gid}.")


# =========================
# Helpers
# =========================
def _strip_accents(s: str) -> str:
    """Remove acentos de string"""
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_text(s) -> str:
    """Normaliza texto: remove acentos, lowercase, espaços extras"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    s = _strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s


def first_token(s) -> str:
    """Retorna primeira palavra normalizada"""
    s = norm_text(s)
    if not s:
        return ""
    return s.split(" ", 1)[0]


def only_digits(s) -> str:
    """Extrai apenas dígitos"""
    return re.sub(r"\D+", "", str(s or ""))


def clean_cell(v) -> str:
    """Limpa valor de célula"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ["nan", "none"]:
        return ""
    return s


def is_missing(v) -> bool:
    """Verifica se valor está vazio"""
    return clean_cell(v) == ""


def parse_date_any(v):
    """Parse flexível de datas em vários formatos"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()

    s = str(v).strip()
    if not s:
        return None

    # Tenta formatos comuns
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    # Fallback com pandas
    try:
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def fmt_date_br(d: date | None) -> str:
    """Formata data no padrão brasileiro"""
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def format_cpf(cpf_value) -> str:
    """Formata CPF com pontos e traço"""
    d = only_digits(cpf_value)
    if len(d) != 11:
        return str(cpf_value or "").strip()
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def cpf_valido(cpf) -> bool:
    """Valida CPF usando algoritmo oficial"""
    cpf = only_digits(cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:  # CPF com todos dígitos iguais
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
    """Valida se telefone tem 11 dígitos"""
    return len(only_digits(value)) == 11


def format_phone_br(value) -> str:
    """Formata telefone no padrão brasileiro"""
    d = only_digits(value)
    if len(d) != 11:
        return str(value or "").strip()
    ddd = d[:2]
    n = d[2:]
    return f"({ddd}) {n[0]}.{n[1:5]}-{n[5:]}"


# =========================
# Sheets read/write
# =========================
@st.cache_data(show_spinner=False, ttl=30)
def load_sheet_df(spreadsheet_id: str, gid: int) -> pd.DataFrame:
    """Carrega dados da planilha com cache de 30 segundos"""
    client = get_gspread_client()
    if client is None:
        return pd.DataFrame()

    try:
        ws = open_worksheet_by_gid(client, spreadsheet_id, gid)
        values = ws.get_all_values()

        if not values:
            ws.append_row(REQUIRED_COLS[:], value_input_option="USER_ENTERED")
            values = ws.get_all_values()

        header = values[0]
        rows = values[1:]
        df = pd.DataFrame(rows, columns=header)

        # Garante que todas as colunas necessárias existem
        for c in REQUIRED_COLS:
            if c not in df.columns:
                df[c] = ""

        # Parse de datas e índice de linha
        df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)
        df["_sheet_row"] = df.index + 2
        return df
    except Exception as e:
        st.error(f"Erro ao carregar planilha: {str(e)}")
        return pd.DataFrame()


def ensure_header_columns(ws, df: pd.DataFrame):
    """Garante que todas as colunas necessárias existem no header"""
    header = ws.row_values(1)
    need_add = [c for c in REQUIRED_COLS if c not in header]
    if need_add:
        ws.update("1:1", [header + need_add])
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""


def build_options_from_df(df: pd.DataFrame, field: str) -> list[str]:
    """Constrói lista de opções únicas a partir dos dados existentes"""
    series = df.get(field, pd.Series([], dtype=str)).fillna("").astype(str).map(lambda x: x.strip())
    vals = [x for x in series.tolist() if x and x.lower() != "nan"]
    uniq = sorted(set(vals), key=lambda x: x.casefold())

    # Opções padrão para evitar listas vazias
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
    """Busca registros por data de nascimento e primeiro nome da mãe"""
    mae_first = first_token(mae)
    if not mae_first:
        return df.iloc[0:0].copy()
    mask_dn = df["_data_nasc_date"] == dn
    mask_mae = df["nome_mae"].apply(lambda x: first_token(x) == mae_first)
    return df[mask_dn & mask_mae].copy()


def gspread_a1(r1, c1, r2, c2):
    """Converte coordenadas numéricas para notação A1"""
    def col_to_a1(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s
    return f"{col_to_a1(c1)}{r1}:{col_to_a1(c2)}{r2}"


def update_row_in_sheet(ws, sheet_row: int, header: list[str], payload: dict):
    """Atualiza linha existente na planilha"""
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


def append_row_in_sheet(ws, header: list[str], payload: dict):
    """Adiciona nova linha na planilha"""
    row = [clean_cell(payload.get(c, "")) for c in header]
    ws.append_row(row, value_input_option="USER_ENTERED")


def next_membro_id(df: pd.DataFrame) -> int:
    """Gera próximo ID de membro"""
    s = df.get("membro_id", pd.Series([], dtype=str)).fillna("").astype(str).map(lambda x: only_digits(x))
    nums = pd.to_numeric(s, errors="coerce")
    if nums.notna().any():
        return int(nums.max()) + 1
    return 1


def validate_required(payload: dict) -> list[str]:
    """Valida campos obrigatórios"""
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


# =========================
# UI setup
# =========================
st.set_page_config(page_title="Igreja - Atualização de Cadastro", page_icon="📘", layout="centered")

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
}
.main, .stApp{
  background: linear-gradient(135deg, #EFF6FF 0%, #FFFFFF 55%, #E0F2FE 100%);
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
  background: white;
  border: 2px solid var(--border);
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
  margin: 14px 0;
}
.section{ font-weight: 900; color: var(--blue2); font-size: 1.15rem; margin-bottom: 10px; }
.small{ color: var(--muted); font-weight: 650; }
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
  transition: all 0.2s ease;
}
div.stButton>button:hover{
  transform: translateY(-2px);
  box-shadow: 0 12px 24px rgba(2, 6, 23, .12);
}
.stTextInput input, .stSelectbox select, .stDateInput input{
  border-radius: 12px !important;
  border: 2px solid #BFDBFE !important;
  padding: 12px !important;
  font-size: 1rem !important;
  transition: border-color 0.2s ease !important;
}
.stTextInput input:focus, .stSelectbox select:focus, .stDateInput input:focus{
  border-color: var(--blue) !important;
  box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.1) !important;
}
hr{
  border: none;
  height: 3px;
  background: linear-gradient(90deg, transparent, #BFDBFE, transparent);
  margin: 18px 0;
}
.miss-wrap{
  border: 2px solid var(--danger);
  background: linear-gradient(135deg, var(--dangerSoft), #FFFFFF);
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
.cong-muted{
  margin-top: 6px;
  font-size: .92rem;
  font-weight: 700;
  color: #64748B;
}
</style>
""",
    unsafe_allow_html=True,
)

# Logo dentro do header
logo_html = ""
if os.path.exists(LOGO_PATH):
    import base64
    with open(LOGO_PATH, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    logo_html = (
        f"<img src='data:image/jpeg;base64,{b64}' "
        "style='width:56px;height:56px;object-fit:contain;border-radius:12px;"
        "background:rgba(255,255,255,.15);padding:6px;' />"
    )

st.markdown(
    f"""
<div class="topbar">
  {logo_html}
  <div class="topbar-title">
    <h1>{APP_TITLE}</h1>
    <p>Digite data de nascimento e o primeiro nome da mãe para encontrar seu cadastro.</p>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# Observação: topbar fica com st.markdown porque precisa estar no fluxo da página principal

# Carrega dados
with st.spinner("Carregando base da igreja..."):
    df = load_sheet_df(SPREADSHEET_ID, WORKSHEET_GID)

if df.empty:
    st.error("Não foi possível carregar os dados. Verifique a conexão com a planilha.")
    st.stop()

# Constrói opções de dropdown
dropdown_opts = {f: build_options_from_df(df, f) for f in DROPDOWN_FIELDS}

# Estado da sessão
if "searched" not in st.session_state:
    st.session_state.searched = False
if "match_ids" not in st.session_state:
    st.session_state.match_ids = []
if "search_dn" not in st.session_state:
    st.session_state.search_dn = None
if "search_mae" not in st.session_state:
    st.session_state.search_mae = ""


def field_block(label: str, missing: bool, *, render_fn):
    """Renderiza campo com destaque se obrigatório e vazio"""
    if missing:
        st.error(f"⚠️ {label} obrigatório", icon="⚠️")
    render_fn()


def dropdown_only(label, field_name, current_value="", key_prefix="x"):
    """Renderiza dropdown com opções dinâmicas"""
    opts = dropdown_opts.get(field_name, []) or ["OUTRO"]
    cur = str(current_value or "").strip().upper()
    
    # Tenta encontrar índice exato ou similar
    idx = 0
    if cur and cur in opts:
        idx = opts.index(cur)
    
    return st.selectbox(
        label,
        options=opts,
        index=idx,
        key=f"{key_prefix}_{field_name}_sel",
    )


def render_found_card(d: dict, total: int):
    """Renderiza card com informações do cadastro encontrado"""
    import html as html_module
    
    dn = parse_date_any(d.get("data_nasc", "")) or st.session_state.search_dn
    mae = clean_cell(d.get("nome_mae", "")) or st.session_state.search_mae
    nome = clean_cell(d.get("nome_completo", "")) or "(Sem nome)"
    cong = clean_cell(d.get("congregacao", ""))
    
    # Escape HTML para evitar problemas com caracteres especiais
    mae_escaped = html_module.escape(mae)
    nome_escaped = html_module.escape(nome)
    cong_escaped = html_module.escape(cong) if cong else "sem informação"
    data_escaped = html_module.escape(fmt_date_br(dn) if dn else "")

    html_content = f"""
    <style>
      .card {{
        background: white;
        border: 2px solid #DBEAFE;
        border-radius: 18px;
        padding: 18px;
        box-shadow: 0 10px 20px rgba(2, 6, 23, .08);
        margin: 14px 0;
      }}
      .section {{
        font-weight: 900;
        color: #0B3AA8;
        font-size: 1.15rem;
        margin-bottom: 10px;
      }}
      .small {{
        color: #475569;
        font-weight: 650;
      }}
      .found-name {{
        margin-top: 10px;
        font-weight: 900;
        color: #0B3AA8;
        font-size: 1.15rem;
      }}
      .cong-muted {{
        margin-top: 6px;
        font-size: .92rem;
        font-weight: 700;
        color: #64748B;
      }}
    </style>
    <div class="card">
      <div class="section">Cadastro encontrado</div>
      <div class="small">Achamos {total} registro(s). Selecione e atualize.</div>

      <div style="margin-top:12px;">
        <div class="small"><b>Data de nascimento</b></div>
        <div style="font-weight:800;color:#0B3AA8;font-size:1.05rem;margin-bottom:10px;">
          {data_escaped}
        </div>

        <div class="small"><b>Nome da mãe</b></div>
        <div style="font-weight:800;color:#0B3AA8;font-size:1.05rem;margin-bottom:12px;">
          {mae_escaped}
        </div>

        <div class="small"><b>Nome</b></div>
        <div class="found-name">{nome_escaped}</div>
        <div class="cong-muted">Congregação: {cong_escaped}</div>
      </div>
    </div>
    """
    components.html(html_content, height=280)


# =========================
# Busca
# =========================
components.html("""
<style>
  .card {
    background: white;
    border: 2px solid #DBEAFE;
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 10px 20px rgba(2, 6, 23, .08);
    margin: 14px 0;
  }
  .section {
    font-weight: 900;
    color: #0B3AA8;
    font-size: 1.15rem;
    margin-bottom: 10px;
  }
</style>
<div class="card">
  <div class="section">Identificação do membro</div>
</div>
""", height=80)

col1, col2 = st.columns(2)
with col1:
    inp_dn = st.date_input(
        "Data de nascimento",
        value=st.session_state.search_dn,
        min_value=date(1900, 1, 1),
        max_value=date.today(),
        format="DD/MM/YYYY",
        help="Selecione sua data de nascimento"
    )
with col2:
    inp_mae = st.text_input(
        "Nome da mãe",
        value=st.session_state.search_mae,
        placeholder="Ex.: Maria",
        help="Digite apenas o primeiro nome"
    )

if st.button("Buscar cadastro"):
    if inp_dn is None:
        st.warning("⚠️ Escolha a data de nascimento.")
    elif not inp_mae.strip():
        st.warning("⚠️ Digite o nome da mãe.")
    else:
        with st.spinner("Buscando..."):
            matches = find_matches(df, inp_dn, inp_mae)
            st.session_state.searched = True
            st.session_state.search_dn = inp_dn
            st.session_state.search_mae = inp_mae.strip()
            st.session_state.match_ids = matches.index.tolist()
            st.rerun()

st.divider()

if not st.session_state.searched:
    st.stop()

match_ids = st.session_state.match_ids

client = get_gspread_client()
if client is None:
    st.stop()

ws = open_worksheet_by_gid(client, SPREADSHEET_ID, WORKSHEET_GID)
ensure_header_columns(ws, df)
header = ws.row_values(1)

# =========================
# Novo cadastro
# =========================
if len(match_ids) == 0:
    components.html("""
<style>
  .card {
    background: white;
    border: 2px solid #DBEAFE;
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 10px 20px rgba(2, 6, 23, .08);
    margin: 14px 0;
  }
  .section {
    font-weight: 900;
    color: #0B3AA8;
    font-size: 1.15rem;
    margin-bottom: 10px;
  }
  .small {
    color: #475569;
    font-weight: 650;
  }
</style>
<div class="card">
  <div class="section">Novo cadastro</div>
  <div class="small">Não encontramos registro. Preencha os dados abaixo.</div>
</div>
""", height=100)

    with st.form("novo_cadastro", clear_on_submit=False):
        dn_val = st.session_state.search_dn
        mae_val = st.session_state.search_mae

        # Campos principais
        st.markdown("### Dados pessoais")
        
        nome_completo = st.text_input(
            "Nome completo *",
            value="",
            key="new_nome",
            placeholder="Digite seu nome completo"
        )

        col_dn, col_cpf = st.columns(2)
        with col_dn:
            data_nasc = st.date_input(
                "Data de nascimento *",
                value=dn_val,
                min_value=date(1900, 1, 1),
                max_value=date.today(),
                format="DD/MM/YYYY",
                key="new_dn"
            )
        with col_cpf:
            cpf_raw = st.text_input(
                "CPF *",
                value="",
                placeholder="000.000.000-00",
                key="new_cpf",
                max_chars=14
            )

        whatsapp_raw = st.text_input(
            "WhatsApp/Telefone *",
            value="",
            placeholder="(88) 9.9999-9999",
            key="new_whats",
            max_chars=15
        )

        st.markdown("### Endereço")
        
        bairro = st.selectbox(
            "Bairro/Distrito *",
            options=BAIRROS_DISTRITOS,
            index=0,
            key="new_bairro"
        )

        endereco = st.text_input(
            "Endereço completo *",
            value="",
            key="new_endereco",
            placeholder="Rua, número, complemento"
        )

        st.markdown("### Filiação")
        
        col_mae, col_pai = st.columns(2)
        with col_mae:
            nome_mae = st.text_input(
                "Nome da mãe *",
                value=mae_val,
                key="new_mae"
            )
        with col_pai:
            nome_pai = st.text_input(
                "Nome do pai",
                value="",
                key="new_pai"
            )

        st.markdown("### Dados complementares")
        
        col_nat, col_nac = st.columns(2)
        with col_nat:
            naturalidade = st.text_input(
                "Naturalidade",
                value="",
                key="new_naturalidade",
                placeholder="Cidade de nascimento"
            )
        with col_nac:
            nacionalidade = dropdown_only(
                "Nacionalidade",
                "nacionalidade",
                "",
                key_prefix="new"
            )

        estado_civil = dropdown_only(
            "Estado civil *",
            "estado_civil",
            "",
            key_prefix="new"
        )

        col_batismo, col_cong = st.columns(2)
        with col_batismo:
            data_batismo = st.text_input(
                "Data do batismo",
                value="",
                placeholder="Ex.: 05/12/1992",
                key="new_batismo"
            )
        with col_cong:
            congregacao = dropdown_only(
                "Congregação *",
                "congregacao",
                "",
                key_prefix="new"
            )

        st.markdown("---")
        st.markdown("**Campos marcados com * são obrigatórios**", help="Preencha todos os campos obrigatórios")
        
        salvar = st.form_submit_button("✓ Salvar novo cadastro", use_container_width=True)

        if salvar:
            cpf_digits = only_digits(cpf_raw)
            phone_digits = only_digits(whatsapp_raw)
            now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")
            novo_id = next_membro_id(df)

            payload = {
                "membro_id": str(novo_id),
                "cod_membro": "",
                "data_nasc": fmt_date_br(data_nasc) if data_nasc else "",
                "nome_mae": nome_mae.strip(),
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

            # Validações
            missing_list = validate_required(payload)
            if missing_list:
                st.error(f"❌ Preencha os campos obrigatórios: {', '.join(missing_list)}")
                st.stop()

            if not cpf_valido(cpf_digits):
                st.error("❌ CPF inválido. Confira e tente de novo.")
                st.stop()

            if not phone_valido(phone_digits):
                st.error("❌ WhatsApp inválido. Precisa ter 11 números (DDD + número).")
                st.stop()

            # Salva
            try:
                with st.spinner("Salvando cadastro..."):
                    append_row_in_sheet(ws, header, payload)
                    st.cache_data.clear()
                    
                st.success(f"✅ Cadastro salvo com sucesso! ID: {novo_id}")
                st.balloons()
                
                # Reset e rerun após 2 segundos
                import time
                time.sleep(2)
                st.session_state.searched = False
                st.session_state.match_ids = []
                st.session_state.search_dn = None
                st.session_state.search_mae = ""
                st.rerun()
            except Exception as e:
                st.error(f"❌ Erro ao salvar: {str(e)}")

    st.stop()

# =========================
# Editar cadastro
# =========================
matches_df = df.loc[match_ids].copy()
total_found = len(matches_df)

# Seleção de múltiplos registros
if total_found > 1:
    matches_df = matches_df.sort_values(by=["nome_completo"], na_position="last")
    options = []
    for idx, r in matches_df.iterrows():
        nome = clean_cell(r.get("nome_completo", "")) or "(Sem nome)"
        cong = clean_cell(r.get("congregacao", ""))
        options.append((idx, f"{nome} | {cong}" if cong else nome))

    sel = st.selectbox(
        "Selecione o membro",
        options=options,
        format_func=lambda x: x[1],
        help="Encontramos mais de um registro. Selecione o correto."
    )
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

with st.form("editar_cadastro", clear_on_submit=False):
    st.markdown("### Dados pessoais")
    
    nome_completo = st.text_input(
        "Nome completo *",
        value=clean_cell(row.get("nome_completo", "")),
        key="edit_nome"
    )

    col_dn, col_cpf = st.columns(2)
    with col_dn:
        data_nasc = st.date_input(
            "Data de nascimento *",
            value=row_dn,
            min_value=date(1900, 1, 1),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="edit_dn"
        )
    with col_cpf:
        cpf_input = st.text_input(
            "CPF *",
            value=format_cpf(row.get("cpf", "")),
            placeholder="000.000.000-00",
            key="edit_cpf",
            max_chars=14
        )

    whatsapp_input = st.text_input(
        "WhatsApp/Telefone *",
        value=format_phone_br(row.get("whatsapp_telefone", "")),
        placeholder="(88) 9.9999-9999",
        key="edit_whats",
        max_chars=15
    )

    st.markdown("### Endereço")
    
    bairro = st.selectbox(
        "Bairro/Distrito *",
        options=BAIRROS_DISTRITOS,
        index=bairro_index,
        key="edit_bairro"
    )

    endereco = st.text_input(
        "Endereço completo *",
        value=clean_cell(row.get("endereco", "")),
        key="edit_endereco"
    )

    st.markdown("### Filiação")
    
    col_mae, col_pai = st.columns(2)
    with col_mae:
        nome_mae = st.text_input(
            "Nome da mãe *",
            value=clean_cell(row.get("nome_mae", "")),
            key="edit_mae"
        )
    with col_pai:
        nome_pai = st.text_input(
            "Nome do pai",
            value=clean_cell(row.get("nome_pai", "")),
            key="edit_pai"
        )

    st.markdown("### Dados complementares")
    
    col_nat, col_nac = st.columns(2)
    with col_nat:
        naturalidade = st.text_input(
            "Naturalidade",
            value=clean_cell(row.get("naturalidade", "")),
            key="edit_naturalidade"
        )
    with col_nac:
        nacionalidade = dropdown_only(
            "Nacionalidade",
            "nacionalidade",
            row.get("nacionalidade", ""),
            key_prefix="edit"
        )

    estado_civil = dropdown_only(
        "Estado civil *",
        "estado_civil",
        row.get("estado_civil", ""),
        key_prefix="edit"
    )

    col_batismo, col_cong = st.columns(2)
    with col_batismo:
        data_batismo = st.text_input(
            "Data do batismo",
            value=clean_cell(row.get("data_batismo", "")),
            key="edit_batismo"
        )
    with col_cong:
        congregacao = dropdown_only(
            "Congregação *",
            "congregacao",
            row.get("congregacao", ""),
            key_prefix="edit"
        )

    st.markdown("---")
    st.markdown("**Campos marcados com * são obrigatórios**")
    
    salvar = st.form_submit_button("✓ Salvar atualização", use_container_width=True)

    if salvar:
        cpf_digits = only_digits(cpf_input)
        phone_digits = only_digits(whatsapp_input)
        now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

        payload = {
            "nome_completo": nome_completo.strip(),
            "data_nasc": fmt_date_br(data_nasc) if data_nasc else "",
            "cpf": format_cpf(cpf_digits),
            "whatsapp_telefone": format_phone_br(phone_digits),
            "bairro_distrito": bairro,
            "endereco": endereco.strip(),
            "nome_mae": nome_mae.strip(),
            "nome_pai": nome_pai.strip(),
            "nacionalidade": nacionalidade,
            "naturalidade": naturalidade.strip(),
            "estado_civil": estado_civil,
            "data_batismo": data_batismo.strip(),
            "congregacao": congregacao,
            "atualizado": now_str,
        }

        # Validações
        missing_list = validate_required(payload)
        if missing_list:
            st.error(f"❌ Preencha os campos obrigatórios: {', '.join(missing_list)}")
            st.stop()

        if not cpf_valido(cpf_digits):
            st.error("❌ CPF inválido. Confira e tente de novo.")
            st.stop()

        if not phone_valido(phone_digits):
            st.error("❌ WhatsApp inválido. Precisa ter 11 números (DDD + número).")
            st.stop()

        # Salva
        try:
            with st.spinner("Salvando alterações..."):
                update_row_in_sheet(ws, sheet_row, header, payload)
                st.cache_data.clear()
                
            st.success("✅ Cadastro atualizado com sucesso!")
            st.balloons()
            
            # Reset e rerun após 2 segundos
            import time
            time.sleep(2)
            st.session_state.searched = False
            st.session_state.match_ids = []
            st.session_state.search_dn = None
            st.session_state.search_mae = ""
            st.rerun()
        except Exception as e:
            st.error(f"❌ Erro ao salvar: {str(e)}")
