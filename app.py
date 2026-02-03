import os
import re
import unicodedata
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# =========================
# Config geral
# =========================
APP_TITLE = "📘 Atualização de Cadastro da Igreja"
TZ = ZoneInfo("America/Fortaleza")

LOGO_PATH = os.path.join("data", "logo_ad.jpg")

# Planilha
SPREADSHEET_ID = "1IUXWrsoBC58-Pe_6mcFQmzgX1xm6GDYvjP1Pd6FH3D0"
WORKSHEET_GID = 1191582738

# Colunas obrigatórias no Sheets
REQUIRED_COLS = [
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

# Lista fixa do Bairro/Distrito
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
# Google Sheets connection
# =========================
def get_gspread_client():
    """
    Autentica com service account.
    Ordem:
    1) st.secrets["gcp_service_account"]
    2) arquivo local service_account.json
    3) GOOGLE_APPLICATION_CREDENTIALS
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception:
        st.error("Dependências ausentes. Instale gspread e google-auth no requirements.txt.")
        return None

    creds_info = None

    if "gcp_service_account" in st.secrets:
        creds_info = st.secrets["gcp_service_account"]
    else:
        sa_path = "service_account.json"
        if os.path.exists(sa_path):
            import json
            with open(sa_path, "r", encoding="utf-8") as f:
                creds_info = json.load(f)

        if creds_info is None and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
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

    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_info(creds_info, scopes=scope)

    import gspread
    return gspread.authorize(credentials)


def open_worksheet_by_gid(client, spreadsheet_id: str, gid: int):
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
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
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
            pass

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
    return len(only_digits(value)) == 11


def format_phone_br(value) -> str:
    d = only_digits(value)
    if len(d) != 11:
        return str(value or "").strip()
    ddd = d[:2]
    n = d[2:]
    return f"({ddd}) {n[0]}.{n[1:5]}-{n[5:]}"


def clean_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ["nan", "none"]:
        return ""
    return s


# =========================
# Carregar dados do Sheets
# =========================
@st.cache_data(show_spinner=False, ttl=30)
def load_sheet_df(spreadsheet_id: str, gid: int) -> pd.DataFrame:
    client = get_gspread_client()
    if client is None:
        return pd.DataFrame()

    ws = open_worksheet_by_gid(client, spreadsheet_id, gid)
    values = ws.get_all_values()

    if not values:
        # cria cabeçalho mínimo
        header = REQUIRED_COLS[:]
        ws.append_row(header, value_input_option="USER_ENTERED")
        values = ws.get_all_values()

    header = values[0]
    rows = values[1:]

    df = pd.DataFrame(rows, columns=header)

    # garante colunas exigidas
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""

    # normaliza datas para filtro
    df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)

    # guarda índice 1-based do Sheets
    df["_sheet_row"] = df.index + 2

    return df


def ensure_header_columns(ws, df: pd.DataFrame):
    header = ws.row_values(1)
    need_add = [c for c in REQUIRED_COLS if c not in header]
    if need_add:
        new_header = header + need_add
        ws.update("1:1", [new_header])
    # garante que df tenha também
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = ""


def build_options_from_df(df: pd.DataFrame, field: str) -> list[str]:
    series = df.get(field, pd.Series([], dtype=str)).fillna("").astype(str).map(lambda x: x.strip())
    vals = [x for x in series.tolist() if x and x.lower() != "nan"]
    uniq = sorted(set(vals), key=lambda x: x.casefold())

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

    if "Outro" not in uniq:
        uniq.append("Outro")

    return uniq


def find_matches(df: pd.DataFrame, dn: date, mae: str) -> pd.DataFrame:
    mae_first = first_token(mae)
    if not mae_first:
        return df.iloc[0:0].copy()
    mask_dn = df["_data_nasc_date"] == dn
    mask_mae = df["nome_mae"].apply(lambda x: first_token(x) == mae_first)
    return df[mask_dn & mask_mae].copy()


def update_row_in_sheet(ws, sheet_row: int, header: list[str], payload: dict):
    """
    Atualiza uma linha inteira, respeitando o cabeçalho.
    Só mexe nas colunas do payload.
    """
    row_values = ws.row_values(sheet_row)
    if len(row_values) < len(header):
        row_values = row_values + [""] * (len(header) - len(row_values))

    for k, v in payload.items():
        if k in header:
            idx = header.index(k)
            row_values[idx] = clean_cell(v)

    # update range completo da linha
    start_col = 1
    end_col = len(header)
    ws.update(
        range_name=gspread_a1(sheet_row, start_col, sheet_row, end_col),
        values=[row_values],
        value_input_option="USER_ENTERED"
    )


def append_row_in_sheet(ws, header: list[str], payload: dict):
    row = [clean_cell(payload.get(c, "")) for c in header]
    ws.append_row(row, value_input_option="USER_ENTERED")


def gspread_a1(r1, c1, r2, c2):
    def col_to_a1(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s
    return f"{col_to_a1(c1)}{r1}:{col_to_a1(c2)}{r2}"


# =========================
# UI
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
}
.main, .stApp{
  background: linear-gradient(135deg, #EFF6FF 0%, #FFFFFF 55%, #E0F2FE 100%);
}
.topbar{
  background: linear-gradient(135deg, var(--blue), var(--blue2));
  color: white;
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
  margin-bottom: 18px;
}
.topbar h1{ margin:0; font-size: 1.35rem; font-weight: 900; }
.topbar p{ margin:.35rem 0 0 0; opacity: .95; font-weight: 600; }
.card{
  background: white;
  border: 2px solid var(--border);
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
  margin: 14px 0;
}
.section{ font-weight: 900; color: var(--blue2); font-size: 1.15rem; margin-bottom: 10px; }
.small{ color: var(--muted); font-weight: 600; }
div.stButton>button{
  background: linear-gradient(135deg, var(--blue), var(--blue2));
  color:#fff; border:none; border-radius: 14px; padding: 12px 18px;
  font-weight: 900; font-size: 1.05rem; width:100%; box-shadow: var(--shadow);
}
.stTextInput input, .stSelectbox select, .stDateInput input{
  border-radius: 12px !important;
  border: 2px solid #BFDBFE !important;
  padding: 12px !important;
  font-size: 1rem !important;
}
hr{ border: none; height: 3px; background: linear-gradient(90deg, transparent, #BFDBFE, transparent); margin: 18px 0; }
.success-box{
  background: linear-gradient(135deg, #ECFDF5, #BBF7D0);
  border: 2px solid #22C55E;
  border-radius: 18px;
  padding: 18px;
  text-align: center;
  box-shadow: var(--shadow);
}
.warn-box{
  background: linear-gradient(135deg, #FFFBEB, #FEF3C7);
  border: 2px solid #F59E0B;
  border-radius: 18px;
  padding: 14px;
  box-shadow: var(--shadow);
}
</style>
""",
    unsafe_allow_html=True,
)

if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, use_container_width=True)

st.markdown(
    f"""
<div class="topbar">
  <h1>{APP_TITLE}</h1>
  <p>Digite data de nascimento e o primeiro nome da mãe para encontrar seu cadastro.</p>
</div>
""",
    unsafe_allow_html=True,
)

# carrega base
with st.spinner("Carregando base da igreja..."):
    df = load_sheet_df(SPREADSHEET_ID, WORKSHEET_GID)

if df.empty:
    st.stop()

# options de dropdowns
dropdown_opts = {f: build_options_from_df(df, f) for f in DROPDOWN_FIELDS}

# estado de busca
if "searched" not in st.session_state:
    st.session_state.searched = False
if "match_ids" not in st.session_state:
    st.session_state.match_ids = []
if "search_dn" not in st.session_state:
    st.session_state.search_dn = None
if "search_mae" not in st.session_state:
    st.session_state.search_mae = ""

st.markdown('<div class="card"><div class="section">🔐 Identificação do membro</div></div>', unsafe_allow_html=True)

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
    )

if st.button("Buscar cadastro"):
    if inp_dn is None:
        st.warning("Escolhe a data de nascimento.")
    elif not inp_mae.strip():
        st.warning("Digite o nome da mãe.")
    else:
        matches = find_matches(df, inp_dn, inp_mae)
        st.session_state.searched = True
        st.session_state.search_dn = inp_dn
        st.session_state.search_mae = inp_mae.strip()
        st.session_state.match_ids = matches.index.tolist()

st.divider()

if not st.session_state.searched:
    st.stop()

match_ids = st.session_state.match_ids

# abre cliente e worksheet para escrita
client = get_gspread_client()
if client is None:
    st.stop()

ws = open_worksheet_by_gid(client, SPREADSHEET_ID, WORKSHEET_GID)
ensure_header_columns(ws, df)
header = ws.row_values(1)

def dropdown_text(label, field_name, current_value="", key_prefix="x"):
    opts = dropdown_opts[field_name]
    cur = str(current_value or "").strip()
    idx = opts.index(cur) if cur in opts else (opts.index("Outro") if "Outro" in opts else 0)
    choice = st.selectbox(label, options=opts, index=idx, key=f"{key_prefix}_{field_name}_sel")
    if choice == "Outro":
        return st.text_input(label, value=cur, key=f"{key_prefix}_{field_name}_txt").strip()
    return choice

# =========================
# Novo cadastro
# =========================
if len(match_ids) == 0:
    st.markdown(
        """
<div class="card">
  <div class="section">🆕 Novo cadastro</div>
  <div class="small">Não achei ninguém com esses dados. Preencha o cadastro abaixo.</div>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.form("novo_cadastro"):
        nome_completo = st.text_input("Nome completo", value="")
        cpf_raw = st.text_input("CPF", value="", placeholder="000.000.000-00")
        whatsapp_raw = st.text_input("WhatsApp/Telefone", value="", placeholder="(88) 9.9999-9999")

        bairro = st.selectbox("Bairro/Distrito", options=BAIRROS_DISTRITOS, index=0)

        endereco = st.text_input("Endereço", value="")
        nome_pai = st.text_input("Nome do pai", value="")
        naturalidade = st.text_input("Naturalidade", value="")

        nacionalidade = dropdown_text("Nacionalidade", "nacionalidade", key_prefix="new")
        estado_civil = dropdown_text("Estado civil", "estado_civil", key_prefix="new")

        data_batismo = st.text_input("Data do batismo", value="", placeholder="Ex.: 05/12/1992")
        congregacao = dropdown_text("Congregação", "congregacao", key_prefix="new")

        st.markdown("---")
        salvar = st.form_submit_button("Salvar novo cadastro")

        if salvar:
            cpf_digits = only_digits(cpf_raw)
            phone_digits = only_digits(whatsapp_raw)

            if not cpf_valido(cpf_digits):
                st.error("CPF inválido. Confira e tente de novo.")
                st.stop()

            if not phone_valido(phone_digits):
                st.error("WhatsApp inválido. Precisa ter 11 números.")
                st.stop()

            now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

            payload = {
                "data_nasc": fmt_date_br(st.session_state.search_dn),
                "nome_mae": st.session_state.search_mae,
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

            append_row_in_sheet(ws, header, payload)

            st.markdown(
                """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Cadastro criado!</div>
  <div style="margin-top:6px; font-weight:700;">Registro salvo na planilha da igreja.</div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.cache_data.clear()

    st.stop()

# =========================
# Editar cadastro existente
# =========================
matches_df = df.loc[match_ids].copy()

st.markdown(
    f"""
<div class="card">
  <div class="section">✅ Cadastro encontrado</div>
  <div class="small">Achamos {len(matches_df)} registro(s). Selecione e atualize.</div>
</div>
""",
    unsafe_allow_html=True,
)

if len(matches_df) > 1:
    matches_df = matches_df.sort_values(by=["nome_completo"], na_position="last")
    options = []
    for idx, r in matches_df.iterrows():
        nome = clean_cell(r.get("nome_completo", "")) or "(Sem nome)"
        cong = clean_cell(r.get("congregacao", ""))
        options.append((idx, f"{nome} | {cong}" if cong else nome))
    sel = st.selectbox("Selecione o membro", options=options, format_func=lambda x: x[1])
    sel_idx = sel[0]
else:
    sel_idx = matches_df.index[0]

row = df.loc[sel_idx].copy()
sheet_row = int(row["_sheet_row"])

st.markdown(
    f"""
<div class="card">
  <div class="section">📄 Confirmação</div>
  <div class="small">
    Data de nascimento {clean_cell(row.get("data_nasc",""))}
    <br>
    Mãe {clean_cell(row.get("nome_mae",""))}
  </div>
</div>
""",
    unsafe_allow_html=True,
)

bairro_current = clean_cell(row.get("bairro_distrito", ""))
bairro_index = BAIRROS_DISTRITOS.index(bairro_current) if bairro_current in BAIRROS_DISTRITOS else 0

with st.form("editar_cadastro"):
    nome_completo = st.text_input("Nome completo", value=clean_cell(row.get("nome_completo", "")))

    cpf_current = format_cpf(row.get("cpf", ""))
    cpf_raw = st.text_input("CPF", value=cpf_current, placeholder="000.000.000-00")

    phone_current = format_phone_br(row.get("whatsapp_telefone", ""))
    whatsapp_raw = st.text_input("WhatsApp/Telefone", value=phone_current, placeholder="(88) 9.9999-9999")

    bairro = st.selectbox("Bairro/Distrito", options=BAIRROS_DISTRITOS, index=bairro_index)

    endereco = st.text_input("Endereço", value=clean_cell(row.get("endereco", "")))
    nome_pai = st.text_input("Nome do pai", value=clean_cell(row.get("nome_pai", "")))
    naturalidade = st.text_input("Naturalidade", value=clean_cell(row.get("naturalidade", "")))

    nacionalidade = dropdown_text("Nacionalidade", "nacionalidade", row.get("nacionalidade", ""), key_prefix="edit")
    estado_civil = dropdown_text("Estado civil", "estado_civil", row.get("estado_civil", ""), key_prefix="edit")

    data_batismo = st.text_input("Data do batismo", value=clean_cell(row.get("data_batismo", "")))
    congregacao = dropdown_text("Congregação", "congregacao", row.get("congregacao", ""), key_prefix="edit")

    st.markdown("---")
    salvar = st.form_submit_button("Salvar atualização")

    if salvar:
        cpf_digits = only_digits(cpf_raw)
        phone_digits = only_digits(whatsapp_raw)

        if not cpf_valido(cpf_digits):
            st.error("CPF inválido. Confira e tente de novo.")
            st.stop()

        if not phone_valido(phone_digits):
            st.error("WhatsApp inválido. Precisa ter 11 números.")
            st.stop()

        now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

        payload = {
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

        update_row_in_sheet(ws, sheet_row, header, payload)

        st.markdown(
            """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Atualização salva!</div>
  <div style="margin-top:6px; font-weight:700;">Dados atualizados na planilha da igreja.</div>
</div>
""",
            unsafe_allow_html=True,
        )
        st.cache_data.clear()
