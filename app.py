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
TZ = ZoneInfo("America/Fortaleza")  # fuso do usuário

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

DROPDOWN_FIELDS = ["bairro_distrito", "congregacao", "nacionalidade", "estado_civil"]


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
    """
    Aceita:
    - date/datetime
    - 'dd/mm/yyyy', 'yyyy-mm-dd', 'dd-mm-yyyy', 'dd/mm/yy'
    - qualquer coisa que o pandas consiga entender
    Retorna date ou None
    """
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
        return pd.to_datetime(s, dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def fmt_date_br(d: date | None) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def load_csv_safely(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    # garante que todas as colunas existam (sem quebrar o arquivo original)
    for c in EDIT_FIELDS:
        if c not in df.columns:
            df[c] = ""
    if "atualizado" not in df.columns:
        df["atualizado"] = ""
    if "membro_id" not in df.columns:
        df["membro_id"] = ""

    # coluna data_nasc é obrigatória pra login
    if "data_nasc" not in df.columns:
        raise ValueError("O CSV precisa ter a coluna 'data_nasc'.")
    if "nome_mae" not in df.columns:
        raise ValueError("O CSV precisa ter a coluna 'nome_mae'.")

    # cria coluna auxiliar de data
    df["_data_nasc_date"] = df["data_nasc"].apply(parse_date_any)
    return df


def save_csv_safely(df: pd.DataFrame, path: str) -> None:
    # remove coluna auxiliar
    if "_data_nasc_date" in df.columns:
        df = df.drop(columns=["_data_nasc_date"])
    # salva em utf-8-sig pra abrir bonito no Excel/LibreOffice
    df.to_csv(path, index=False, encoding="utf-8-sig")


def get_csv_path() -> str:
    # prioridade: mesma pasta do app
    local_path = os.path.join(os.getcwd(), CSV_FILENAME)
    if os.path.exists(local_path):
        return local_path

    # fallback do ambiente daqui
    alt_path = os.path.join("/mnt/data", CSV_FILENAME)
    if os.path.exists(alt_path):
        return alt_path

    return local_path  # vai criar se não existir


def build_options(df: pd.DataFrame, field: str) -> list[str]:
    base = []
    if field in df.columns:
        base = (
            df[field]
            .fillna("")
            .astype(str)
            .map(lambda x: x.strip())
            .tolist()
        )
    base = [x for x in base if x and x.lower() != "nan"]
    uniq = sorted(set(base), key=lambda x: x.casefold())

    # adiciona sugestões padrão quando o CSV ainda tá vazio nesse campo
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

    # sempre permite "Outro"
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
.topbar h1{
  margin:0;
  font-size: 1.35rem;
  font-weight: 900;
  letter-spacing: .2px;
}
.topbar p{
  margin:.35rem 0 0 0;
  opacity: .95;
  font-weight: 600;
}
.card{
  background: var(--card);
  border: 2px solid var(--border);
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
  margin: 14px 0;
}
.section{
  font-weight: 900;
  color: var(--blue2);
  font-size: 1.15rem;
  margin-bottom: 10px;
}
.small{
  color: var(--muted);
  font-weight: 600;
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
}
div.stButton>button:hover{
  transform: translateY(-1px);
  filter: brightness(1.05);
}
.stTextInput input, .stSelectbox select, .stDateInput input{
  border-radius: 12px !important;
  border: 2px solid #BFDBFE !important;
  padding: 12px !important;
  font-size: 1rem !important;
}
hr{
  border: none;
  height: 3px;
  background: linear-gradient(90deg, transparent, #BFDBFE, transparent);
  margin: 18px 0;
}
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

st.markdown(
    f"""
<div class="topbar">
  <h1>{APP_TITLE}</h1>
  <p>Entre com data de nascimento e primeiro nome da mãe. Atualize seu cadastro em 1 minuto.</p>
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

# opções pros dropdowns (puxadas do próprio CSV)
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
        "Nome da mãe (digite só o primeiro nome se quiser)",
        placeholder="Ex.: Maria",
    )

btn = st.button("Buscar cadastro")

def find_matches(df_: pd.DataFrame, dn: date, mae: str) -> pd.DataFrame:
    mae_first = first_token(mae)
    if not mae_first:
        return df_.iloc[0:0].copy()

    # bate por data_nasc + primeiro token da mãe
    mask_dn = df_["_data_nasc_date"] == dn
    mask_mae = df_["nome_mae"].apply(lambda x: first_token(x) == mae_first)
    out = df_[mask_dn & mask_mae].copy()
    return out

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
            cpf = st.text_input("CPF", value="", placeholder="Somente números se possível")
            whatsapp = st.text_input("WhatsApp/Telefone", value="", placeholder="Ex.: 88999998888")

            # dropdown com opção "Outro"
            def dropdown_or_text(label, field_name):
                opts = dropdown_opts[field_name]
                choice = st.selectbox(label, options=opts, index=opts.index("Outro") if "Outro" in opts else 0)
                other = ""
                if choice == "Outro":
                    other = st.text_input(f"{label} (digite)", value="")
                return other.strip() if choice == "Outro" else choice

            bairro = dropdown_or_text("Bairro/Distrito", "bairro_distrito")
            endereco = st.text_input("Endereço", value="")
            nome_pai = st.text_input("Nome do pai", value="")
            naturalidade = st.text_input("Naturalidade", value="")
            nacionalidade = dropdown_or_text("Nacionalidade", "nacionalidade")
            estado_civil = dropdown_or_text("Estado civil", "estado_civil")
            data_batismo = st.text_input("Data do batismo", value="", placeholder="Ex.: 05/12/1992")
            congregacao = dropdown_or_text("Congregação", "congregacao")

            st.markdown("---")
            salvar = st.form_submit_button("Salvar novo cadastro")

            if salvar:
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
                new_row["cpf"] = only_digits(cpf)
                new_row["whatsapp_telefone"] = only_digits(whatsapp)
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

                # recarrega auxiliar e salva
                df2["_data_nasc_date"] = df2["data_nasc"].apply(parse_date_any)
                save_csv_safely(df2, csv_path)

                st.markdown(
                    """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Cadastro criado!</div>
  <div style="margin-top:6px; font-weight:700;">Seu registro foi salvo no arquivo da igreja.</div>
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
        for idx, row in matches.iterrows():
            nome = str(row.get("nome_completo", "")).strip() or "(Sem nome)"
            cong = str(row.get("congregacao", "")).strip()
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
        # campos editáveis pedidos
        nome_completo = st.text_input("Nome completo", value=str(row.get("nome_completo", "") or ""))
        cpf = st.text_input("CPF", value=str(row.get("cpf", "") or ""), placeholder="Somente números se possível")
        whatsapp = st.text_input("WhatsApp/Telefone", value=str(row.get("whatsapp_telefone", "") or ""))

        def dropdown_or_text_edit(label, field_name, current_value):
            opts = dropdown_opts[field_name]
            cur = str(current_value or "").strip()
            # tenta achar o índice, senão cai em "Outro"
            if cur and cur in opts:
                idx = opts.index(cur)
            else:
                idx = opts.index("Outro") if "Outro" in opts else 0
            choice = st.selectbox(label, options=opts, index=idx)
            if choice == "Outro":
                return st.text_input(f"{label} (digite)", value=cur)
            return choice

        bairro = dropdown_or_text_edit("Bairro/Distrito", "bairro_distrito", row.get("bairro_distrito", ""))
        endereco = st.text_input("Endereço", value=str(row.get("endereco", "") or ""))
        nome_pai = st.text_input("Nome do pai", value=str(row.get("nome_pai", "") or ""))
        naturalidade = st.text_input("Naturalidade", value=str(row.get("naturalidade", "") or ""))
        nacionalidade = dropdown_or_text_edit("Nacionalidade", "nacionalidade", row.get("nacionalidade", ""))
        estado_civil = dropdown_or_text_edit("Estado civil", "estado_civil", row.get("estado_civil", ""))
        data_batismo = st.text_input("Data do batismo", value=str(row.get("data_batismo", "") or ""), placeholder="Ex.: 05/12/1992")
        congregacao = dropdown_or_text_edit("Congregação", "congregacao", row.get("congregacao", ""))

        st.markdown("---")
        salvar = st.form_submit_button("Salvar atualização")

        if salvar:
            now_str = datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")

            df.loc[sel_idx, "nome_completo"] = nome_completo.strip()
            df.loc[sel_idx, "cpf"] = only_digits(cpf)
            df.loc[sel_idx, "whatsapp_telefone"] = only_digits(whatsapp)
            df.loc[sel_idx, "bairro_distrito"] = str(bairro).strip()
            df.loc[sel_idx, "endereco"] = endereco.strip()
            df.loc[sel_idx, "nome_pai"] = nome_pai.strip()
            df.loc[sel_idx, "nacionalidade"] = str(nacionalidade).strip()
            df.loc[sel_idx, "naturalidade"] = naturalidade.strip()
            df.loc[sel_idx, "estado_civil"] = str(estado_civil).strip()
            df.loc[sel_idx, "data_batismo"] = data_batismo.strip()
            df.loc[sel_idx, "congregacao"] = str(congregacao).strip()
            df.loc[sel_idx, "atualizado"] = now_str

            # salva
            save_csv_safely(df, csv_path)

            st.markdown(
                """
<div class="success-box">
  <div style="font-size:2rem; font-weight:900;">✅ Atualização salva!</div>
  <div style="margin-top:6px; font-weight:700;">Os dados foram gravados no arquivo Dados_membros.csv.</div>
</div>
""",
                unsafe_allow_html=True,
            )
