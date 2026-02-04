import os
import re
import json
import base64
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional, Literal, Any
from functools import lru_cache
from enum import Enum

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Lazy imports para performance
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    st.error("📦 Instale: `pip install gspread google-auth`")


# ============================================================================
# CONFIGURAÇÕES E ENUMS
# ============================================================================

class EstadoCivil(str, Enum):
    """Estados civis válidos"""
    SOLTEIRO = "SOLTEIRO"
    CASADO = "CASADO"
    UNIAO_ESTAVEL = "UNIÃO ESTÁVEL"
    DIVORCIADO = "DIVORCIADO"
    VIUVO = "VIÚVO"
    OUTRO = "OUTRO"


class Nacionalidade(str, Enum):
    """Nacionalidades comuns"""
    BRASILEIRA = "BRASILEIRA"
    BRASILEIRO = "BRASILEIRO"
    OUTRA = "OUTRA"


@dataclass(frozen=True)
class Config:
    """Configurações centralizadas e imutáveis"""

    # App
    TITLE: str = "Sistema de Cadastro - Assembleia de Deus"
    VERSION: str = "2.0.0"
    ICON: str = "⛪"
    LOGO_PATH: str = "data/logo_ad.jpg"

    # Timezone
    TZ: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Fortaleza"))

    # Google Sheets
    SPREADSHEET_ID: str = "1IUXWrsoBC58-Pe_6mcFQmzgX1xm6GDYvjP1Pd6FH3D0"
    WORKSHEET_GID: int = 1191582738
    CACHE_TTL: int = 30

    # Schema
    SCHEMA: tuple = (
        "membro_id", "cod_membro", "data_nasc", "nome_mae", "nome_completo",
        "cpf", "whatsapp_telefone", "bairro_distrito", "endereco", "nome_pai",
        "nacionalidade", "naturalidade", "estado_civil", "data_batismo",
        "congregacao", "atualizado"
    )

    # Campos obrigatórios
    REQUIRED: dict = field(default_factory=lambda: {
        "nome_completo": "Nome completo",
        "cpf": "CPF",
        "data_nasc": "Data de nascimento",
        "whatsapp_telefone": "WhatsApp/Telefone",
        "bairro_distrito": "Bairro/Distrito",
        "endereco": "Endereço",
        "nome_mae": "Nome da mãe",
        "estado_civil": "Estado civil",
        "congregacao": "Congregação",
    })

    # Bairros de Iguatu-CE
    BAIRROS: tuple = (
        "Argentina Siqueira", "Belém", "Berilândia", "Centro", "Cohab",
        "Conjunto Esperança", "Damião Carneiro", "Depósito", 
        "Distrito Industrial", "Duque De Caxias",
        "Edmilson Correia De Vasconcelos", "Encantado", "Jaime Lopes",
        "José Aurélio Câmara", "Lacerda", "Manituba", "Maravilha",
        "Monteiro De Morais", "Nenelândia", "Passagem", "Paus Branco",
        "Salviano Carlos", "São Miguel", "Sede Rural", "Uruquê",
        "Vila Betânia", "Vila São Paulo"
    )

    # Validações
    MIN_BIRTH_DATE: date = date(1900, 1, 1)
    CPF_LENGTH: int = 11
    PHONE_LENGTH: int = 11
    DATETIME_FORMAT: str = "%d/%m/%Y %H:%M:%S"


CFG = Config()


# ============================================================================
# UTILITÁRIOS DE TEXTO
# ============================================================================

class TextUtils:
    """Processamento de texto com cache para performance"""

    @staticmethod
    @lru_cache(maxsize=2048)
    def strip_accents(text: str) -> str:
        """Remove acentos (cached)"""
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c))

    @classmethod
    def normalize(cls, text: Any) -> str:
        """Normaliza: remove acentos, lowercase, espaços"""
        if not text or (isinstance(text, float) and pd.isna(text)):
            return ""

        text = str(text).strip()
        text = cls.strip_accents(text)
        text = text.lower()
        return re.sub(r'\s+', ' ', text)

    @classmethod
    def first_token(cls, text: str) -> str:
        """Primeira palavra normalizada"""
        normalized = cls.normalize(text)
        return normalized.split(' ', 1)[0] if normalized else ""

    @staticmethod
    def only_digits(value: Any) -> str:
        """Extrai apenas dígitos"""
        return re.sub(r'\D', '', str(value or ''))

    @staticmethod
    def clean(value: Any) -> str:
        """Limpa valores de células"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        cleaned = str(value).strip()
        return "" if cleaned.lower() in ('nan', 'none', 'null') else cleaned

    @staticmethod
    def is_empty(value: Any) -> bool:
        """Verifica se está vazio"""
        return len(TextUtils.clean(value)) == 0


# ============================================================================
# VALIDADORES
# ============================================================================

@dataclass
class ValidationResult:
    """Resultado de validação"""
    is_valid: bool
    message: str = ""

    def __bool__(self) -> bool:
        return self.is_valid


class Validators:
    """Validadores de dados"""

    @staticmethod
    def cpf(cpf_input: str) -> ValidationResult:
        """Valida CPF (algoritmo oficial)"""
        digits = TextUtils.only_digits(cpf_input)

        if len(digits) != 11:
            return ValidationResult(False, "CPF deve ter 11 dígitos")

        if digits == digits[0] * 11:
            return ValidationResult(False, "CPF com dígitos repetidos inválido")

        def calc_digit(base: str, weights: range) -> str:
            total = sum(int(d) * w for d, w in zip(base, weights))
            remainder = total % 11
            return str(0 if remainder < 2 else 11 - remainder)

        base = digits[:9]
        d1 = calc_digit(base, range(10, 1, -1))
        d2 = calc_digit(base + d1, range(11, 1, -1))

        if digits != base + d1 + d2:
            return ValidationResult(False, "CPF inválido")

        return ValidationResult(True)

    @staticmethod
    def phone(phone_input: str) -> ValidationResult:
        """Valida telefone brasileiro"""
        digits = TextUtils.only_digits(phone_input)

        if len(digits) != 11:
            return ValidationResult(False, "Telefone deve ter 11 dígitos (DDD + número)")

        ddd = int(digits[:2])
        if not (11 <= ddd <= 99):
            return ValidationResult(False, f"DDD {ddd} inválido")

        if digits[2] != '9':
            return ValidationResult(False, "Número deve ser celular (começar com 9)")

        return ValidationResult(True)

    @staticmethod
    def birth_date(birth_date: Optional[date]) -> ValidationResult:
        """Valida data de nascimento"""
        if not birth_date:
            return ValidationResult(False, "Data de nascimento obrigatória")

        if birth_date < CFG.MIN_BIRTH_DATE:
            return ValidationResult(False, "Data muito antiga")

        if birth_date > date.today():
            return ValidationResult(False, "Data no futuro não permitida")

        from datetime import timedelta
        min_birth = date.today() - timedelta(days=365)
        if birth_date > min_birth:
            return ValidationResult(False, "Idade mínima: 1 ano")

        return ValidationResult(True)


# ============================================================================
# FORMATADORES
# ============================================================================

class Formatters:
    """Formatadores brasileiros"""

    @staticmethod
    def cpf(cpf_input: str) -> str:
        """000.000.000-00"""
        digits = TextUtils.only_digits(cpf_input)
        if len(digits) != 11:
            return cpf_input
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"

    @staticmethod
    def phone(phone_input: str) -> str:
        """(88) 9.9999-9999"""
        digits = TextUtils.only_digits(phone_input)
        if len(digits) != 11:
            return phone_input
        return f"({digits[:2]}) {digits[2]}.{digits[3:7]}-{digits[7:]}"

    @staticmethod
    def date_br(date_obj: Optional[date]) -> str:
        """dd/mm/yyyy"""
        return date_obj.strftime("%d/%m/%Y") if date_obj else ""

    @staticmethod
    def parse_date(value: Any) -> Optional[date]:
        """Parse flexível de datas"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None

        if isinstance(value, date):
            return value if not isinstance(value, datetime) else value.date()

        text = str(value).strip()
        if not text:
            return None

        formats = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d"]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        try:
            parsed = pd.to_datetime(text, dayfirst=True, errors='coerce')
            return None if pd.isna(parsed) else parsed.date()
        except Exception:
            return None


# ============================================================================
# GOOGLE SHEETS SERVICE
# ============================================================================

class SheetsService:
    """Gerenciador Google Sheets (Singleton)"""

    _instance = None
    _client = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def client(self):
        """Cliente autenticado (cached)"""
        if self._client:
            return self._client

        if not GSPREAD_AVAILABLE:
            return None

        creds = self._load_credentials()
        if not creds:
            st.error("🔐 Configure credenciais do Google no st.secrets")
            return None

        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file"
            ]
            credentials = Credentials.from_service_account_info(creds, scopes=scopes)
            self._client = gspread.authorize(credentials)
            return self._client
        except Exception as e:
            st.error(f"❌ Erro ao autenticar: {e}")
            return None

    @staticmethod
    def _load_credentials() -> Optional[dict]:
        """Carrega credenciais (prioridade: secrets > arquivo > env)"""
        # 1. Streamlit Secrets
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])

        # 2. Arquivo local
        if os.path.exists("service_account.json"):
            with open("service_account.json", 'r') as f:
                return json.load(f)

        # 3. Env var
        env_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if env_path and os.path.exists(env_path):
            with open(env_path, 'r') as f:
                return json.load(f)

        return None

    def get_worksheet(self):
        """Obtém worksheet pelo GID"""
        if not self.client:
            return None

        try:
            sheet = self.client.open_by_key(CFG.SPREADSHEET_ID)
            for ws in sheet.worksheets():
                if int(ws.id) == CFG.WORKSHEET_GID:
                    return ws
            raise gspread.WorksheetNotFound(f"GID {CFG.WORKSHEET_GID} não encontrado")
        except Exception as e:
            st.error(f"❌ Erro ao acessar planilha: {e}")
            return None

    @staticmethod
    @st.cache_data(ttl=CFG.CACHE_TTL, show_spinner=False)
    def load_dataframe(_worksheet) -> pd.DataFrame:
        """Carrega dados (cached 30s)"""
        if not _worksheet:
            return pd.DataFrame()

        try:
            values = _worksheet.get_all_values()

            if not values:
                _worksheet.append_row(list(CFG.SCHEMA), value_input_option="USER_ENTERED")
                values = _worksheet.get_all_values()

            header, *rows = values
            df = pd.DataFrame(rows, columns=header)

            for col in CFG.SCHEMA:
                if col not in df.columns:
                    df[col] = ""

            df["_sheet_row"] = range(2, len(df) + 2)
            return df
        except Exception as e:
            st.error(f"❌ Erro ao carregar: {e}")
            return pd.DataFrame()

    @staticmethod
    def append_row(worksheet, data: dict) -> bool:
        """Adiciona linha"""
        if not worksheet:
            return False

        try:
            header = worksheet.row_values(1)
            row = [TextUtils.clean(data.get(col, "")) for col in header]
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"❌ Erro ao adicionar: {e}")
            return False

    @staticmethod
    def update_row(worksheet, row_num: int, data: dict) -> bool:
        """Atualiza linha"""
        if not worksheet:
            return False

        try:
            header = worksheet.row_values(1)
            current = worksheet.row_values(row_num)

            if len(current) < len(header):
                current.extend([""] * (len(header) - len(current)))

            for col, value in data.items():
                if col in header:
                    idx = header.index(col)
                    current[idx] = TextUtils.clean(value)

            end_col = SheetsService._num_to_col(len(header))
            range_notation = f"A{row_num}:{end_col}{row_num}"

            worksheet.update(range_notation, [current], value_input_option="USER_ENTERED")
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"❌ Erro ao atualizar: {e}")
            return False

    @staticmethod
    def _num_to_col(n: int) -> str:
        """1=A, 27=AA"""
        result = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            result = chr(65 + r) + result
        return result


# ============================================================================
# LÓGICA DE NEGÓCIO
# ============================================================================

def find_members(df: pd.DataFrame, birth_date: date, mother_name: str) -> pd.DataFrame:
    """Busca membros por data de nascimento e nome da mãe"""
    mother_first = TextUtils.first_token(mother_name)

    if not mother_first:
        return df.iloc[0:0].copy()

    # Adiciona coluna de data parseada se não existir
    if "_birth_date" not in df.columns:
        df["_birth_date"] = df["data_nasc"].apply(Formatters.parse_date)

    mask_date = df["_birth_date"] == birth_date
    mask_mother = df["nome_mae"].apply(lambda x: TextUtils.first_token(x) == mother_first)

    return df[mask_date & mask_mother].copy()


def validate_member_data(data: dict) -> tuple[bool, list[str]]:
    """Valida dados completos de membro"""
    errors = []

    # Campos obrigatórios
    for field, label in CFG.REQUIRED.items():
        value = data.get(field)
        if field == "data_nasc":
            if not value or not isinstance(value, date):
                errors.append(f"{label} é obrigatório")
        else:
            if TextUtils.is_empty(value):
                errors.append(f"{label} é obrigatório")

    # CPF
    cpf_result = Validators.cpf(data.get('cpf', ''))
    if not cpf_result:
        errors.append(cpf_result.message)

    # Telefone
    phone_result = Validators.phone(data.get('whatsapp_telefone', ''))
    if not phone_result:
        errors.append(phone_result.message)

    # Data nascimento
    date_result = Validators.birth_date(data.get('data_nasc'))
    if not date_result:
        errors.append(date_result.message)

    # Nome completo
    full_name = data.get('nome_completo', '').strip()
    if len(full_name.split()) < 2:
        errors.append("Nome completo deve ter nome e sobrenome")

    return (len(errors) == 0, errors)


def get_next_member_id(df: pd.DataFrame) -> int:
    """Gera próximo ID de membro"""
    if df.empty or "membro_id" not in df.columns:
        return 1

    ids = df["membro_id"].apply(TextUtils.only_digits)
    ids = pd.to_numeric(ids, errors='coerce')

    if ids.notna().any():
        return int(ids.max()) + 1
    return 1


def build_dropdown_options(df: pd.DataFrame, field: str) -> list[str]:
    """Constrói opções de dropdown a partir dos dados"""
    if df.empty or field not in df.columns:
        return []

    values = df[field].fillna("").astype(str).str.strip()
    values = values[values != ""].tolist()
    unique = sorted(set(values), key=str.casefold)

    # Defaults
    if field == "nacionalidade":
        defaults = ["BRASILEIRA", "BRASILEIRO", "OUTRA"]
        for d in defaults:
            if d not in unique:
                unique.append(d)

    elif field == "estado_civil":
        defaults = [e.value for e in EstadoCivil]
        for d in defaults:
            if d not in unique:
                unique.append(d)

    return unique or ["OUTRO"]


# ============================================================================
# UI COMPONENTS
# ============================================================================

def render_css():
    """CSS moderno"""
    st.markdown("""
    <style>
    :root {
        --primary: #1D4ED8;
        --primary-dark: #0B3AA8;
        --muted: #475569;
        --border: #DBEAFE;
        --shadow: 0 10px 20px rgba(2, 6, 23, .08);
    }

    .main, .stApp {
        background: linear-gradient(135deg, #EFF6FF 0%, #FFF 55%, #E0F2FE 100%);
    }

    .card {
        background: white;
        border: 2px solid var(--border);
        border-radius: 18px;
        padding: 18px;
        box-shadow: var(--shadow);
        margin: 14px 0;
    }

    .section-title {
        font-weight: 900;
        color: var(--primary-dark);
        font-size: 1.15rem;
        margin-bottom: 10px;
    }

    .stTextInput input, .stSelectbox select, .stDateInput input {
        border-radius: 12px !important;
        border: 2px solid #BFDBFE !important;
        padding: 12px !important;
        transition: all 0.2s ease !important;
    }

    .stTextInput input:focus, .stSelectbox select:focus {
        border-color: var(--primary) !important;
        box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.1) !important;
    }

    div.stButton > button {
        background: linear-gradient(135deg, var(--primary), var(--primary-dark));
        color: white;
        border: none;
        border-radius: 14px;
        padding: 12px 18px;
        font-weight: 900;
        width: 100%;
        box-shadow: var(--shadow);
        transition: transform 0.2s ease;
    }

    div.stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 24px rgba(2, 6, 23, .12);
    }
    </style>
    """, unsafe_allow_html=True)


def render_header(title: str):
    """Header com logo"""
    logo_html = ""

    if os.path.exists(CFG.LOGO_PATH):
        with open(CFG.LOGO_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
            logo_html = f"""
            <img src='data:image/jpeg;base64,{b64}' 
                 style='width:56px;height:56px;object-fit:contain;
                        border-radius:12px;background:rgba(255,255,255,.15);
                        padding:6px;' />
            """

    st.markdown(f"""
    <div style='background:linear-gradient(135deg,#1D4ED8,#0B3AA8);
                color:white;border-radius:18px;padding:16px 18px;
                box-shadow:0 10px 20px rgba(2,6,23,.08);margin-bottom:18px;
                display:flex;align-items:center;gap:12px;'>
        {logo_html}
        <div>
            <h1 style='margin:0;font-size:1.25rem;font-weight:900;line-height:1.1;'>
                {title}
            </h1>
            <p style='margin:0;opacity:.95;font-weight:650;'>
                Digite data de nascimento e o primeiro nome da mãe
            </p>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_card_header(title: str, subtitle: str = ""):
    """Card header"""
    components.html(f"""
    <div style="background:white;border:2px solid #DBEAFE;border-radius:18px;
                padding:18px;box-shadow:0 10px 20px rgba(2,6,23,.08);margin:14px 0;">
        <div style="font-weight:900;color:#0B3AA8;font-size:1.15rem;margin-bottom:10px;">
            {title}
        </div>
        {f'<div style="color:#475569;font-weight:650;">{subtitle}</div>' if subtitle else ''}
    </div>
    """, height=80 if subtitle else 60)


def render_member_preview(member: dict, total_found: int):
    """Preview do membro encontrado"""
    import html

    nome = html.escape(TextUtils.clean(member.get("nome_completo", "")) or "(Sem nome)")
    cong = html.escape(TextUtils.clean(member.get("congregacao", "")) or "sem informação")
    mae = html.escape(TextUtils.clean(member.get("nome_mae", "")))
    dn = Formatters.parse_date(member.get("data_nasc"))
    data_str = html.escape(Formatters.date_br(dn))

    components.html(f"""
    <div style="background:white;border:2px solid #DBEAFE;border-radius:18px;
                padding:18px;box-shadow:0 10px 20px rgba(2,6,23,.08);margin:14px 0;">
        <div style="font-weight:900;color:#0B3AA8;font-size:1.15rem;margin-bottom:10px;">
            Cadastro encontrado
        </div>
        <div style="color:#475569;font-weight:650;margin-bottom:12px;">
            Encontramos {total_found} registro(s)
        </div>

        <div style="margin-top:12px;">
            <div style="color:#475569;font-weight:650;"><b>Data de nascimento</b></div>
            <div style="font-weight:800;color:#0B3AA8;font-size:1.05rem;margin-bottom:10px;">
                {data_str}
            </div>

            <div style="color:#475569;font-weight:650;"><b>Nome da mãe</b></div>
            <div style="font-weight:800;color:#0B3AA8;font-size:1.05rem;margin-bottom:12px;">
                {mae}
            </div>

            <div style="color:#475569;font-weight:650;"><b>Nome</b></div>
            <div style="font-weight:900;color:#0B3AA8;font-size:1.15rem;">
                {nome}
            </div>
            <div style="margin-top:6px;font-size:.92rem;font-weight:700;color:#64748B;">
                Congregação: {cong}
            </div>
        </div>
    </div>
    """, height=300)


# ============================================================================
# FORMULÁRIOS
# ============================================================================

def render_member_form(
    mode: Literal["new", "edit"],
    initial_data: Optional[dict] = None,
    dropdown_opts: Optional[dict] = None
) -> Optional[dict]:
    """Renderiza formulário de membro (novo ou edição)"""

    initial_data = initial_data or {}
    dropdown_opts = dropdown_opts or {}

    key_prefix = f"{mode}_"

    # Dados iniciais
    birth_date = Formatters.parse_date(initial_data.get("data_nasc"))

    with st.form(f"{mode}_member_form", clear_on_submit=False):
        st.markdown("### 📋 Dados pessoais")

        nome = st.text_input(
            "Nome completo *",
            value=TextUtils.clean(initial_data.get("nome_completo", "")),
            key=f"{key_prefix}nome",
            placeholder="Nome completo sem abreviações"
        )

        col1, col2 = st.columns(2)
        with col1:
            data_nasc = st.date_input(
                "Data de nascimento *",
                value=birth_date,
                min_value=CFG.MIN_BIRTH_DATE,
                max_value=date.today(),
                format="DD/MM/YYYY",
                key=f"{key_prefix}dn"
            )

        with col2:
            cpf_input = st.text_input(
                "CPF *",
                value=Formatters.cpf(initial_data.get("cpf", "")),
                placeholder="000.000.000-00",
                key=f"{key_prefix}cpf",
                max_chars=14
            )

        whats_input = st.text_input(
            "WhatsApp/Telefone *",
            value=Formatters.phone(initial_data.get("whatsapp_telefone", "")),
            placeholder="(88) 9.9999-9999",
            key=f"{key_prefix}whats",
            max_chars=15
        )

        st.markdown("### 📍 Endereço")

        bairro_current = TextUtils.clean(initial_data.get("bairro_distrito", ""))
        bairro_idx = CFG.BAIRROS.index(bairro_current) if bairro_current in CFG.BAIRROS else 0

        bairro = st.selectbox(
            "Bairro/Distrito *",
            options=CFG.BAIRROS,
            index=bairro_idx,
            key=f"{key_prefix}bairro"
        )

        endereco = st.text_input(
            "Endereço completo *",
            value=TextUtils.clean(initial_data.get("endereco", "")),
            key=f"{key_prefix}endereco",
            placeholder="Rua, número, complemento"
        )

        st.markdown("### 👨‍👩‍👧 Filiação")

        col1, col2 = st.columns(2)
        with col1:
            mae = st.text_input(
                "Nome da mãe *",
                value=TextUtils.clean(initial_data.get("nome_mae", "")),
                key=f"{key_prefix}mae"
            )

        with col2:
            pai = st.text_input(
                "Nome do pai",
                value=TextUtils.clean(initial_data.get("nome_pai", "")),
                key=f"{key_prefix}pai"
            )

        st.markdown("### 📝 Dados complementares")

        col1, col2 = st.columns(2)
        with col1:
            naturalidade = st.text_input(
                "Naturalidade",
                value=TextUtils.clean(initial_data.get("naturalidade", "")),
                key=f"{key_prefix}nat",
                placeholder="Cidade de nascimento"
            )

        with col2:
            nac_opts = dropdown_opts.get("nacionalidade", ["BRASILEIRA", "BRASILEIRO", "OUTRA"])
            nac_current = TextUtils.clean(initial_data.get("nacionalidade", "")).upper()
            nac_idx = nac_opts.index(nac_current) if nac_current in nac_opts else 0

            nacionalidade = st.selectbox(
                "Nacionalidade",
                options=nac_opts,
                index=nac_idx,
                key=f"{key_prefix}nac"
            )

        ec_opts = dropdown_opts.get("estado_civil", [e.value for e in EstadoCivil])
        ec_current = TextUtils.clean(initial_data.get("estado_civil", "")).upper()
        ec_idx = ec_opts.index(ec_current) if ec_current in ec_opts else 0

        estado_civil = st.selectbox(
            "Estado civil *",
            options=ec_opts,
            index=ec_idx,
            key=f"{key_prefix}ec"
        )

        col1, col2 = st.columns(2)
        with col1:
            batismo = st.text_input(
                "Data do batismo",
                value=TextUtils.clean(initial_data.get("data_batismo", "")),
                key=f"{key_prefix}bat",
                placeholder="Ex.: 05/12/1992"
            )

        with col2:
            cong_opts = dropdown_opts.get("congregacao", ["SEDE", "OUTRA"])
            cong_current = TextUtils.clean(initial_data.get("congregacao", "")).upper()
            cong_idx = cong_opts.index(cong_current) if cong_current in cong_opts else 0

            congregacao = st.selectbox(
                "Congregação *",
                options=cong_opts,
                index=cong_idx,
                key=f"{key_prefix}cong"
            )

        st.divider()
        st.caption("**Campos marcados com * são obrigatórios**")

        submit_label = "✓ Salvar novo cadastro" if mode == "new" else "✓ Salvar atualização"
        submitted = st.form_submit_button(submit_label, use_container_width=True)

        if submitted:
            return {
                "nome_completo": nome.strip(),
                "data_nasc": data_nasc,
                "cpf": cpf_input,
                "whatsapp_telefone": whats_input,
                "bairro_distrito": bairro,
                "endereco": endereco.strip(),
                "nome_mae": mae.strip(),
                "nome_pai": pai.strip(),
                "nacionalidade": nacionalidade,
                "naturalidade": naturalidade.strip(),
                "estado_civil": estado_civil,
                "data_batismo": batismo.strip(),
                "congregacao": congregacao,
            }

        return None


# ============================================================================
# MAIN APP
# ============================================================================

def initialize_session():
    """Inicializa estado da sessão"""
    defaults = {
        "searched": False,
        "match_ids": [],
        "search_dn": None,
        "search_mae": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main():
    """Aplicação principal"""

    # Config página
    st.set_page_config(
        page_title=CFG.TITLE,
        page_icon=CFG.ICON,
        layout="centered"
    )

    # UI
    render_css()
    render_header(CFG.TITLE)

    # Inicializa
    initialize_session()

    # Conecta sheets
    sheets = SheetsService()
    worksheet = sheets.get_worksheet()

    if not worksheet:
        st.stop()

    # Carrega dados
    with st.spinner("🔄 Carregando base de dados..."):
        df = SheetsService.load_dataframe(worksheet)

    if df.empty:
        st.error("❌ Não foi possível carregar os dados")
        st.stop()

    # Dropdown options
    dropdown_opts = {
        "nacionalidade": build_dropdown_options(df, "nacionalidade"),
        "estado_civil": build_dropdown_options(df, "estado_civil"),
        "congregacao": build_dropdown_options(df, "congregacao"),
    }

    # ========================================
    # BUSCA
    # ========================================

    render_card_header("🔍 Identificação do membro")

    col1, col2 = st.columns(2)

    with col1:
        input_date = st.date_input(
            "Data de nascimento",
            value=st.session_state.search_dn,
            min_value=CFG.MIN_BIRTH_DATE,
            max_value=date.today(),
            format="DD/MM/YYYY",
            help="Sua data de nascimento"
        )

    with col2:
        input_mother = st.text_input(
            "Nome da mãe",
            value=st.session_state.search_mae,
            placeholder="Ex.: Maria",
            help="Apenas o primeiro nome"
        )

    if st.button("🔍 Buscar cadastro", use_container_width=True):
        if not input_date:
            st.warning("⚠️ Escolha a data de nascimento")
        elif not input_mother.strip():
            st.warning("⚠️ Digite o nome da mãe")
        else:
            with st.spinner("🔎 Buscando..."):
                matches = find_members(df, input_date, input_mother)

                st.session_state.searched = True
                st.session_state.search_dn = input_date
                st.session_state.search_mae = input_mother.strip()
                st.session_state.match_ids = matches.index.tolist()

                st.rerun()

    if not st.session_state.searched:
        st.stop()

    st.divider()

    match_ids = st.session_state.match_ids

    # ========================================
    # NOVO CADASTRO
    # ========================================

    if len(match_ids) == 0:
        render_card_header(
            "➕ Novo cadastro",
            "Não encontramos seu registro. Preencha os dados abaixo."
        )

        form_data = render_member_form(
            mode="new",
            initial_data={
                "data_nasc": st.session_state.search_dn,
                "nome_mae": st.session_state.search_mae,
            },
            dropdown_opts=dropdown_opts
        )

        if form_data:
            # Validar
            is_valid, errors = validate_member_data(form_data)

            if not is_valid:
                for err in errors:
                    st.error(f"❌ {err}")
                st.stop()

            # Preparar payload
            now_str = datetime.now(CFG.TZ).strftime(CFG.DATETIME_FORMAT)
            new_id = get_next_member_id(df)

            payload = {
                "membro_id": str(new_id),
                "cod_membro": "",
                "data_nasc": Formatters.date_br(form_data["data_nasc"]),
                "nome_mae": form_data["nome_mae"],
                "nome_completo": form_data["nome_completo"],
                "cpf": Formatters.cpf(form_data["cpf"]),
                "whatsapp_telefone": Formatters.phone(form_data["whatsapp_telefone"]),
                "bairro_distrito": form_data["bairro_distrito"],
                "endereco": form_data["endereco"],
                "nome_pai": form_data["nome_pai"],
                "nacionalidade": form_data["nacionalidade"],
                "naturalidade": form_data["naturalidade"],
                "estado_civil": form_data["estado_civil"],
                "data_batismo": form_data["data_batismo"],
                "congregacao": form_data["congregacao"],
                "atualizado": now_str,
            }

            # Salvar
            with st.spinner("💾 Salvando..."):
                if SheetsService.append_row(worksheet, payload):
                    st.success(f"✅ Cadastro salvo! ID: {new_id}")
                    st.balloons()

                    import time
                    time.sleep(2)

                    # Reset
                    st.session_state.searched = False
                    st.session_state.match_ids = []
                    st.session_state.search_dn = None
                    st.session_state.search_mae = ""
                    st.rerun()

        st.stop()

    # ========================================
    # EDITAR CADASTRO
    # ========================================

    matches_df = df.loc[match_ids].copy()
    total_found = len(matches_df)

    # Seleção (se múltiplos)
    if total_found > 1:
        matches_df = matches_df.sort_values("nome_completo")

        options = []
        for idx, row in matches_df.iterrows():
            nome = TextUtils.clean(row.get("nome_completo", "")) or "(Sem nome)"
            cong = TextUtils.clean(row.get("congregacao", ""))
            label = f"{nome} | {cong}" if cong else nome
            options.append((idx, label))

        selected = st.selectbox(
            "Selecione o membro",
            options=options,
            format_func=lambda x: x[1],
            help="Múltiplos registros encontrados"
        )
        selected_idx = selected[0]
    else:
        selected_idx = matches_df.index[0]

    # Preview
    row_data = df.loc[selected_idx].to_dict()
    render_member_preview(row_data, total_found)

    # Formulário de edição
    sheet_row = int(row_data["_sheet_row"])

    form_data = render_member_form(
        mode="edit",
        initial_data=row_data,
        dropdown_opts=dropdown_opts
    )

    if form_data:
        # Validar
        is_valid, errors = validate_member_data(form_data)

        if not is_valid:
            for err in errors:
                st.error(f"❌ {err}")
            st.stop()

        # Preparar payload
        now_str = datetime.now(CFG.TZ).strftime(CFG.DATETIME_FORMAT)

        payload = {
            "nome_completo": form_data["nome_completo"],
            "data_nasc": Formatters.date_br(form_data["data_nasc"]),
            "cpf": Formatters.cpf(form_data["cpf"]),
            "whatsapp_telefone": Formatters.phone(form_data["whatsapp_telefone"]),
            "bairro_distrito": form_data["bairro_distrito"],
            "endereco": form_data["endereco"],
            "nome_mae": form_data["nome_mae"],
            "nome_pai": form_data["nome_pai"],
            "nacionalidade": form_data["nacionalidade"],
            "naturalidade": form_data["naturalidade"],
            "estado_civil": form_data["estado_civil"],
            "data_batismo": form_data["data_batismo"],
            "congregacao": form_data["congregacao"],
            "atualizado": now_str,
        }

        # Atualizar
        with st.spinner("💾 Salvando alterações..."):
            if SheetsService.update_row(worksheet, sheet_row, payload):
                st.success("✅ Cadastro atualizado com sucesso!")
                st.balloons()

                import time
                time.sleep(2)

                # Reset
                st.session_state.searched = False
                st.session_state.match_ids = []
                st.session_state.search_dn = None
                st.session_state.search_mae = ""
                st.rerun()


if __name__ == "__main__":
    main()
