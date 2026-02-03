import os
import re
import unicodedata
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# =========================================================
# Config
# =========================================================
APP_TITLE = "📘 Atualização de Cadastro da Igreja"
CSV_FILENAME = "Dados_membros.csv"
TZ = ZoneInfo("America/Fortaleza")

LOGO_PATH = os.path.join("data", "logo_ad.jpg")

EDIT_FIELDS = [
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

# Aqui continua dropdown: congregacao, nacionalidade, estado_civil
DROPDOWN_FIELDS = ["congregacao", "nacionalidade", "estado_civil"]


# =========================================================
# Helpers
# =========================================================
def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_text(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    s = _strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s


def first_token(s: str) -> str:
    s = norm_text(s)
    if not s:
        return ""
    return s.split(" ", 1)[0]


def only_digits(s: str) -> str:
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


# ===== CPF: máscara + validação =====
def format_cpf(cpf_digits: str) -> str:
    d = only_digits(cpf_digits)
    if len(d) != 11:
        return cpf_digits.strip()
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def cpf_valido(cpf: str) -> bool:
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


# ===== Telefone: validação 11 dígitos + formatação =====
def format_phone_br(digits: str) -> str:
    d = only_digits(digits)
    if len(d) != 11:
        return digits.strip()
    ddd = d[:2]
    n = d[2:]
    # (88) 9.9999-9999
    return f"({ddd}) {n[0]}.{n[1:5]}-{n[5:]}"


def phone_valido(digits: str) -> bool:
    d = only_digits(digits)
    return len(d) == 11


def load_csv_safely(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")

    # garante colunas
    for c in EDIT_FIELDS:
        if c not in df.columns:
            df[c] = ""
    if "atualizado" not in df.columns:
        df["atualizado"] = ""
    if "membro_id" not in df.columns:
        df["membro_id"] = ""

    if "data_nasc" not in df.columns:
        raise ValueError("O CSV precisa ter a coluna 'data_nasc'.")
    if "nome_mae" not in df.columns:
        raise ValueError("O CSV precisa ter a coluna 'nome_mae'.")

    df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)
    return df


def save_csv_safely(df: pd.DataFrame, path: str) -> None:
    if "_data_nasc_date" in df.columns:
        df = df.drop(columns=["_data_nasc_date"])
    df.to_csv(path, index=False, encoding="utf-8-sig")


def get_csv_path() -> str:
    local_path = os.path.join(os.getcwd(), CSV_FILENAME)
    if os.path.exists(local_path):
        return local_path

    alt_path = os.path.join("/mnt/data", CSV_FILENAME)
    if os.path.exists(alt_path):
        return alt_path

    return local_path


def build_options(df: pd.DataFrame, field: str) -> list[str]:
    base = []
    if field in df.columns:
        base = df[field].fillna("").astype(str).map(lambda x: x.strip()).tolist()
    base = [x for x in base if x and x.lower() != "nan"]
    uniq = sorted(set(base), key=lambda x: x.casefold())

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


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="Igreja - Atualização de Cadastro", page_icon="📘", layout="centered")

st.markdown(
    """
<style>
:root{
  --blue:#1D4ED8;
  --blue2:#0B3AA8;
  --blueSoft:#EFF6FF;
  --text:#0F172A;
  --muted:#475569;
  --card:#FFFFFF;
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
  padding: 18px 18px;
  box-shadow: var(--shadow);
  margin-bottom: 18px;
}
.topbar h1{ margin:0; font-size: 1.35rem; font-weight: 900; letter-spacing: .2px; }
.topbar p{ margin:.35rem 0 0 0; opacity: .95; font-weight: 600; }
.card{
  background: var(--card);
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
div.stButton>button:hover{ transform: translateY(-1px); filter: brightness(1.05); }
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

# Logo
if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, use_container_width=True)

st.markdown(
    f"""
<div class="topbar">
  <h1>{APP_TITLE}</h1>
  <p>Entre com data de nascimento e o primeiro nome da mãe. Atualize seu cadastro rápido.</p>
</div>
""",
    unsafe_allow_html=True,
)

csv_path = get_csv_path()

if not os.path.exists(csv_path):
    st.markdown(
        """
<div class="warn-box">
  <b>Atenção.</b> Não encontrei o arquivo Dados_membros.csv na pasta.
  <div style="margin-top:6px">Coloque o CSV junto do app.py e recarregue.</div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.stop()

try:
    df = load_csv_safely(csv_path)
except Exception as e:
    st.error(f"Não consegui abrir o CSV. Erro: {e}")
    st.stop()

dropdown_opts = {f: build_options(df, f) for f in DROPDOWN_FIELDS}

st.markdown('<div class="card"><div class="section">🔐 Identificação do membro</div></div>', unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    inp_dn = st.date_input(
        "Data de nascimento",
        value=None,
        min_value=date(1900, 1, 1),
        max_value=date.today(),
        format="DD/MM/YYYY",
    )
with col2:
    inp_mae = st.text_input(
        "Nome da mãe (pelo menos o primeiro nome)",
        placeholder="Ex.: Maria",
    )

btn = st.button("Buscar cadastro")


def find_matches(df_: pd.DataFrame, dn: date, mae: str) -> pd.DataFrame:
    mae_first = first_token(mae)
    if not mae_first:
        return df_.iloc[0:0].copy()
    mask_dn = df_["_data_nasc_date"] == dn
    mask_mae = df_["nome_mae"].apply(lambda x: first_token(x) == mae_first)
    return df_[mask_dn & mask_mae].copy()


if btn:
    if inp_dn is None:
        st.warning("Escolhe a data de nascimento primeiro.")
        st.stop()
    if not inp_mae or not inp_mae.strip():
        st.warning("Digite o nome da mãe (pelo menos o primeiro nome).")
        st.stop()

    matches = find_matches(df, inp_dn, inp_mae)

    st.divider()

    # =========================================================
    # Caso 1: não achou -> novo cadastro
    # =========================================================
    if matches.empty:
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

            def dropdown_or_text(label, field_name, current_value=""):
                opts = dropdown_opts[field_name]
                cur = str(current_value or "").strip()
                idx = opts.index(cur) if cur in opts else (opts.index("Outro") if "Outro" in opts else 0)
                choice = st.selectbox(label, options=opts, index=idx)
                if choice == "Outro":
                    return st.text_input(f"{label}", value=cur).strip()
                return choice

            nacionalidade = dropdown_or_text("Nacionalidade", "nacionalidade")
            estado_civil = dropdown_or_text("Estado civil", "estado_civil")

            data_batismo = st.text_input("Data do batismo", value="", placeholder="Ex.: 05/12/1992")
            congregacao = dropdown_or_text("Congregação", "congregacao")

            st.markdown("---")
            salvar = st.form_submit_button("Salvar novo cadastro")

            if salvar:
                cpf_digits = only_digits(cpf_raw)
                phone_digits = only_digits(whatsapp_raw)

                if not cpf_valido(cpf_digits):
                    st.error("CPF inválido. Confira e tente de novo.")
                    st.stop()

                if not phone_valido(phone_digits):
                    st.error("WhatsApp/Telefone inválido. Precisa ter 11 números. Ex.: (88) 9.9999-9999")
                    st.stop()

                # novo id
                try:
                    existing_ids = pd.to_numeric(df.get("membro_id", pd.Series([])), errors="coerce")
                    next_id = int(existing_ids.max()) + 1 if existing_ids.notna().any() else 1
                except Exception:
                    next_id = len(df) + 1

                now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

                new_row = {c: "" for c in df.columns if c != "_data_nasc_date"}
                new_row["membro_id"] = next_id
                new_row["data_nasc"] = fmt_date_br(inp_dn)
                new_row["nome_mae"] = str(inp_mae).strip()
                new_row["nome_completo"] = nome_completo.strip()
                new_row["cpf"] = format_cpf(cpf_digits)
                new_row["whatsapp_telefone"] = format_phone_br(phone_digits)
                new_row["bairro_distrito"] = bairro
                new_row["endereco"] = endereco.strip()
                new_row["nome_pai"] = nome_pai.strip()
                new_row["nacionalidade"] = nacionalidade
                new_row["naturalidade"] = naturalidade.strip()
                new_row["estado_civil"] = estado_civil
                new_row["data_batismo"] = data_batismo.strip()
                new_row["congregacao"] = congregacao
                new_row["atualizado"] = now_str

                df2 = df.drop(columns=["_data_nasc_date"]).copy()
                df2 = pd.concat([df2, pd.DataFrame([new_row])], ignore_index=True)
                df2["_data_nasc_date"] = df2["data_nasc"].apply(parse_date_any)

                save_csv_safely(df2, csv_path)

                st.markdown(
                    """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Cadastro criado!</div>
  <div style="margin-top:6px; font-weight:700;">Registro salvo no Dados_membros.csv</div>
</div>
""",
                    unsafe_allow_html=True,
                )

        st.stop()

    # =========================================================
    # Caso 2: achou -> editar
    # =========================================================
    st.markdown(
        f"""
<div class="card">
  <div class="section">✅ Cadastro encontrado</div>
  <div class="small">Achamos {len(matches)} registro(s). Selecione e atualize os dados.</div>
</div>
""",
        unsafe_allow_html=True,
    )

    if len(matches) > 1:
        matches = matches.sort_values(by=["nome_completo"], na_position="last")
        options = []
        for idx, r in matches.iterrows():
            nome = str(r.get("nome_completo", "")).strip() or "(Sem nome)"
            cong = str(r.get("congregacao", "")).strip()
            options.append((idx, f"{nome} | {cong}" if cong else nome))

        sel = st.selectbox("Selecione o membro", options=options, format_func=lambda x: x[1])
        sel_idx = sel[0]
    else:
        sel_idx = matches.index[0]

    row = df.loc[sel_idx].copy()

    st.markdown(
        f"""
<div class="card">
  <div class="section">📄 Dados atuais</div>
  <div class="small">
    <b>Data de nascimento:</b> {row.get("data_nasc","")} &nbsp; | &nbsp;
    <b>Mãe:</b> {row.get("nome_mae","")}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.form("editar_cadastro"):
        nome_completo = st.text_input("Nome completo", value=str(row.get("nome_completo", "") or ""))

        cpf_current = format_cpf(row.get("cpf", ""))
        cpf_raw = st.text_input("CPF", value=cpf_current, placeholder="000.000.000-00")

        phone_current = str(row.get("whatsapp_telefone", "") or "")
        whatsapp_raw = st.text_input("WhatsApp/Telefone", value=phone_current, placeholder="(88) 9.9999-9999")

        # bairro fixo, sem “Outro”
        bairro_current = str(row.get("bairro_distrito", "") or "").strip()
        bairro_index = BAIRROS_DISTRITOS.index(bairro_current) if bairro_current in BAIRROS_DISTRITOS else 0
        bairro = st.selectbox("Bairro/Distrito", options=BAIRROS_DISTRITOS, index=bairro_index)

        endereco = st.text_input("Endereço", value=str(row.get("endereco", "") or ""))
        nome_pai = st.text_input("Nome do pai", value=str(row.get("nome_pai", "") or ""))
        naturalidade = st.text_input("Naturalidade", value=str(row.get("naturalidade", "") or ""))

        def dropdown_or_text_edit(label, field_name, current_value):
            opts = dropdown_opts[field_name]
            cur = str(current_value or "").strip()
            idx = opts.index(cur) if cur in opts else (opts.index("Outro") if "Outro" in opts else 0)
            choice = st.selectbox(label, options=opts, index=idx)
            if choice == "Outro":
                return st.text_input(f"{label}", value=cur).strip()
            return choice

        nacionalidade = dropdown_or_text_edit("Nacionalidade", "nacionalidade", row.get("nacionalidade", ""))
        estado_civil = dropdown_or_text_edit("Estado civil", "estado_civil", row.get("estado_civil", ""))
        data_batismo = st.text_input("Data do batismo", value=str(row.get("data_batismo", "") or ""), placeholder="Ex.: 05/12/1992")
        congregacao = dropdown_or_text_edit("Congregação", "congregacao", row.get("congregacao", ""))

        st.markdown("---")
        salvar = st.form_submit_button("Salvar atualização")

        if salvar:
            cpf_digits = only_digits(cpf_raw)
            phone_digits = only_digits(whatsapp_raw)

            if not cpf_valido(cpf_digits):
                st.error("CPF inválido. Confira e tente de novo.")
                st.stop()

            if not phone_valido(phone_digits):
                st.error("WhatsApp/Telefone inválido. Precisa ter 11 números. Ex.: (88) 9.9999-9999")
                st.stop()

            now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

            df.loc[sel_idx, "nome_completo"] = nome_completo.strip()
            df.loc[sel_idx, "cpf"] = format_cpf(cpf_digits)
            df.loc[sel_idx, "whatsapp_telefone"] = format_phone_br(phone_digits)
            df.loc[sel_idx, "bairro_distrito"] = bairro
            df.loc[sel_idx, "endereco"] = endereco.strip()
            df.loc[sel_idx, "nome_pai"] = nome_pai.strip()
            df.loc[sel_idx, "nacionalidade"] = str(nacionalidade).strip()
            df.loc[sel_idx, "naturalidade"] = naturalidade.strip()
            df.loc[sel_idx, "estado_civil"] = str(estado_civil).strip()
            df.loc[sel_idx, "data_batismo"] = data_batismo.strip()
            df.loc[sel_idx, "congregacao"] = str(congregacao).strip()
            df.loc[sel_idx, "atualizado"] = now_str

            save_csv_safely(df, csv_path)

            st.markdown(
                """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Atualização salva!</div>
  <div style="margin-top:6px; font-weight:700;">Dados gravados no Dados_membros.csv</div>
</div>
""",
                unsafe_allow_html=True,
            )
