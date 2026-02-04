import os
import re
import json
import base64
import unicodedata
import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Literal, Any
from functools import lru_cache, wraps
from enum import Enum
from time import time, sleep

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# Lazy imports
try:
    import gspread
    from google.oauth2.service_account import Credentials
    from gspread.exceptions import APIError, SpreadsheetNotFound
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    st.error("📦 Instale: `pip install gspread google-auth`")

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# DECORADORES
# ============================================================================

def retry_on_failure(max_attempts: int = 3, delay: float = 1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Falha após {max_attempts} tentativas: {e}")
                        raise
                    logger.warning(f"Tentativa {attempt + 1} falhou: {e}. Retentando...")
                    sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

def rate_limit(max_calls: int = 10, time_window: int = 60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"rate_limit_{func.__name__}"
            now = time()

            if key not in st.session_state:
                st.session_state[key] = []

            st.session_state[key] = [
                t for t in st.session_state[key] 
                if now - t < time_window
            ]

            if len(st.session_state[key]) >= max_calls:
                st.error("⏱️ Muitas requisições. Aguarde um momento.")
                return None

            st.session_state[key].append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

def measure_time(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time()
        result = func(*args, **kwargs)
        elapsed = time() - start
        logger.info(f"{func.__name__} executado em {elapsed:.3f}s")
        return result
    return wrapper

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================

class EstadoCivil(str, Enum):
    SOLTEIRO = "SOLTEIRO"
    CASADO = "CASADO"
    UNIAO_ESTAVEL = "UNIÃO ESTÁVEL"
    DIVORCIADO = "DIVORCIADO"
    VIUVO = "VIÚVO"
    OUTRO = "OUTRO"

class Nacionalidade(str, Enum):
    BRASILEIRA = "BRASILEIRA"
    BRASILEIRO = "BRASILEIRO"
    OUTRA = "OUTRA"

@dataclass(frozen=True)
class Config:
    TITLE: str = "Sistema de Cadastro - Assembleia de Deus"
    VERSION: str = "3.0.0"
    ICON: str = "⛪"
    LOGO_PATH: str = "data/logo_ad.jpg"
    TZ: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Fortaleza"))
    SPREADSHEET_ID: str = "1IUXWrsoBC58-Pe_6mcFQmzgX1xm6GDYvjP1Pd6FH3D0"
    WORKSHEET_GID: int = 1191582738
    CACHE_TTL: int = 60
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0
    RATE_LIMIT_CALLS: int = 10
    RATE_LIMIT_WINDOW: int = 60

    SCHEMA: tuple = (
        "membro_id", "cod_membro", "data_nasc", "nome_mae", "nome_completo",
        "cpf", "whatsapp_telefone", "bairro_distrito", "endereco", "nome_pai",
        "nacionalidade", "naturalidade", "estado_civil", "data_batismo",
        "congregacao", "atualizado"
    )

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

    MIN_BIRTH_DATE: date = date(1900, 1, 1)
    CPF_LENGTH: int = 11
    PHONE_LENGTH: int = 11
    DATETIME_FORMAT: str = "%d/%m/%Y %H:%M:%S"

CFG = Config()

# ============================================================================
# UTILITÁRIOS DE TEXTO
# ============================================================================

class TextUtils:
    @staticmethod
    @lru_cache(maxsize=2048)
    def strip_accents(text: str) -> str:
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c))

    @classmethod
    def normalize(cls, text: Any) -> str:
        if not text or (isinstance(text, float) and pd.isna(text)):
            return ""
        text = str(text).strip()
        text = cls.strip_accents(text)
        text = text.lower()
        return re.sub(r'\s+', ' ', text)

    @classmethod
    def first_token(cls, text: str) -> str:
        normalized = cls.normalize(text)
        return normalized.split(' ', 1)[0] if normalized else ""

    @staticmethod
    def only_digits(value: Any) -> str:
        return re.sub(r'\D', '', str(value or ''))

    @staticmethod
    def clean(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        cleaned = str(value).strip()
        return "" if cleaned.lower() in ('nan', 'none', 'null') else cleaned

    @staticmethod
    def is_empty(value: Any) -> bool:
        return len(TextUtils.clean(value)) == 0

    @staticmethod
    def sanitize_input(value: str, max_length: int = 200) -> str:
        if not value:
            return ""
        sanitized = re.sub(r'[<>\"\'%;()&+]', '', str(value))
        return sanitized[:max_length].strip()

# ============================================================================
# VALIDADORES
# ============================================================================

@dataclass
class ValidationResult:
    is_valid: bool
    message: str = ""

    def __bool__(self) -> bool:
        return self.is_valid

class Validators:
    @staticmethod
    def cpf(cpf_input: str) -> ValidationResult:
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
        if not birth_date:
            return ValidationResult(False, "Data de nascimento obrigatória")

        if birth_date < CFG.MIN_BIRTH_DATE:
            return ValidationResult(False, "Data muito antiga")

        if birth_date > date.today():
            return ValidationResult(False, "Data no futuro não permitida")

        min_birth = date.today() - timedelta(days=365)
        if birth_date > min_birth:
            return ValidationResult(False, "Idade mínima: 1 ano")

        return ValidationResult(True)

# ============================================================================
# FORMATADORES
# ============================================================================

class Formatters:
    @staticmethod
    def cpf(cpf_input: str) -> str:
        digits = TextUtils.only_digits(cpf_input)
        if len(digits) != 11:
            return cpf_input
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"

    @staticmethod
    def phone(phone_input: str) -> str:
        digits = TextUtils.only_digits(phone_input)
        if len(digits) != 11:
            return phone_input
        return f"({digits[:2]}) {digits[2]}.{digits[3:7]}-{digits[7:]}"

    @staticmethod
    def date_br(date_obj: Optional[date]) -> str:
        return date_obj.strftime("%d/%m/%Y") if date_obj else ""

    @staticmethod
    def parse_date(value: Any) -> Optional[date]:
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
    _instance = None
    _client = None
    _lock = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            try:
                from threading import Lock
                cls._lock = Lock()
            except ImportError:
                cls._lock = None
        return cls._instance

    @property
    def client(self):
        if self._client:
            return self._client

        if self._lock:
            with self._lock:
                if self._client:
                    return self._client
                return self._authenticate()
        else:
            return self._authenticate()

    def _authenticate(self):
        if not GSPREAD_AVAILABLE:
            return None

        creds = self._load_credentials()
        if not creds:
            logger.error("Credenciais não encontradas")
            st.error("🔐 Configure credenciais do Google no st.secrets")
            return None

        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file"
            ]
            credentials = Credentials.from_service_account_info(creds, scopes=scopes)
            self._client = gspread.authorize(credentials)
            logger.info("Cliente Google Sheets autenticado com sucesso")
            return self._client
        except Exception as e:
            logger.error(f"Erro na autenticação: {e}")
            st.error(f"❌ Erro ao autenticar: {e}")
            return None

    @staticmethod
    def _load_credentials() -> Optional[dict]:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])

        if os.path.exists("service_account.json"):
            with open("service_account.json", 'r') as f:
                return json.load(f)

        env_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if env_path and os.path.exists(env_path):
            with open(env_path, 'r') as f:
                return json.load(f)

        return None

    @retry_on_failure(max_attempts=CFG.MAX_RETRIES, delay=CFG.RETRY_DELAY)
    def get_worksheet(self):
        if not self.client:
            return None

        try:
            sheet = self.client.open_by_key(CFG.SPREADSHEET_ID)
            for ws in sheet.worksheets():
                if int(ws.id) == CFG.WORKSHEET_GID:
                    logger.info(f"Worksheet {CFG.WORKSHEET_GID} encontrada")
                    return ws
            raise SpreadsheetNotFound(f"GID {CFG.WORKSHEET_GID} não encontrado")
        except Exception as e:
            logger.error(f"Erro ao acessar planilha: {e}")
            st.error(f"❌ Erro ao acessar planilha: {e}")
            return None

    @staticmethod
    @st.cache_data(ttl=CFG.CACHE_TTL, show_spinner=False)
    @measure_time
    def load_dataframe(_worksheet) -> pd.DataFrame:
        if not _worksheet:
            return pd.DataFrame()

        try:
            values = _worksheet.get_all_values()

            if not values:
                logger.warning("Planilha vazia - criando header")
                _worksheet.append_row(list(CFG.SCHEMA), value_input_option="USER_ENTERED")
                values = _worksheet.get_all_values()

            header, *rows = values
            df = pd.DataFrame(rows, columns=header)

            for col in CFG.SCHEMA:
                if col not in df.columns:
                    df[col] = ""

            df["_sheet_row"] = range(2, len(df) + 2)
            df["_birth_date"] = df["data_nasc"].apply(Formatters.parse_date)

            logger.info(f"Carregados {len(df)} registros")
            return df
        except Exception as e:
            logger.error(f"Erro ao carregar dados: {e}")
            st.error(f"❌ Erro ao carregar: {e}")
            return pd.DataFrame()

    @staticmethod
    @retry_on_failure(max_attempts=CFG.MAX_RETRIES, delay=CFG.RETRY_DELAY)
    def append_row(worksheet, data: dict) -> bool:
        if not worksheet:
            return False

        try:
            header = worksheet.row_values(1)
            row = [TextUtils.clean(data.get(col, "")) for col in header]
            worksheet.append_row(row, value_input_option="USER_ENTERED")
            st.cache_data.clear()
            logger.info(f"Linha adicionada: membro_id={data.get('membro_id')}")
            return True
        except Exception as e:
            logger.error(f"Erro ao adicionar: {e}")
            st.error(f"❌ Erro ao adicionar: {e}")
            return False

    @staticmethod
    @retry_on_failure(max_attempts=CFG.MAX_RETRIES, delay=CFG.RETRY_DELAY)
    def update_row(worksheet, row_num: int, data: dict) -> bool:
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
            logger.info(f"Linha {row_num} atualizada")
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar: {e}")
            st.error(f"❌ Erro ao atualizar: {e}")
            return False

    @staticmethod
    def _num_to_col(n: int) -> str:
        result = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            result = chr(65 + r) + result
        return result

# ============================================================================
# LÓGICA DE NEGÓCIO
# ============================================================================

@measure_time
def find_members(df: pd.DataFrame, birth_date: date, mother_name: str) -> pd.DataFrame:
    mother_first = TextUtils.first_token(mother_name)

    if not mother_first or len(mother_first) < 2:
        logger.warning("Nome da mãe muito curto")
        return df.iloc[0:0].copy()

    mask_date = df["_birth_date"] == birth_date
    mask_mother = df["nome_mae"].apply(lambda x: TextUtils.first_token(x) == mother_first)

    result = df[mask_date & mask_mother].copy()
    logger.info(f"Encontrados {len(result)} registros")
    return result

def validate_member_data(data: dict) -> tuple[bool, list[str]]:
    errors = []

    sanitized_data = {
        k: TextUtils.sanitize_input(v) if isinstance(v, str) else v
        for k, v in data.items()
    }

    for field, label in CFG.REQUIRED.items():
        value = sanitized_data.get(field)
        if field == "data_nasc":
            if not value or not isinstance(value, date):
                errors.append(f"{label} é obrigatório")
        else:
            if TextUtils.is_empty(value):
                errors.append(f"{label} é obrigatório")

    cpf_result = Validators.cpf(sanitized_data.get('cpf', ''))
    if not cpf_result:
        errors.append(cpf_result.message)

    phone_result = Validators.phone(sanitized_data.get('whatsapp_telefone', ''))
    if not phone_result:
        errors.append(phone_result.message)

    date_result = Validators.birth_date(sanitized_data.get('data_nasc'))
    if not date_result:
        errors.append(date_result.message)

    full_name = sanitized_data.get('nome_completo', '').strip()
    if len(full_name.split()) < 2:
        errors.append("Nome completo deve ter nome e sobrenome")

    return (len(errors) == 0, errors)

def get_next_member_id(df: pd.DataFrame) -> int:
    if df.empty or "membro_id" not in df.columns:
        return 1

    ids = df["membro_id"].apply(TextUtils.only_digits)
    ids = pd.to_numeric(ids, errors='coerce')

    if ids.notna().any():
        next_id = int(ids.max()) + 1
        logger.info(f"Próximo ID: {next_id}")
        return next_id
    return 1

@st.cache_data(ttl=300)
def build_dropdown_options(df_hash: str, field: str) -> list[str]:
    df = st.session_state.get('_cached_df')
    if df is None or df.empty or field not in df.columns:
        return []

    values = df[field].fillna("").astype(str).str.strip()
    values = values[values != ""].tolist()
    unique = sorted(set(values), key=str.casefold)

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

    /* Campos normais */
    .stTextInput > div > div > input,
    .stSelectbox > div > div > select,
    .stDateInput > div > div > input {
        border-radius: 12px !important;
        border: 2px solid #BFDBFE !important;
        padding: 12px !important;
        transition: all 0.2s ease !important;
    }

    .stTextInput > div > div > input:focus,
    .stSelectbox > div > div > select:focus,
    .stDateInput > div > div > input:focus {
        border-color: var(--primary) !important;
        box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.1) !important;
    }

    /* Animação de pulso */
    @keyframes glow-pulse {
        0%, 100% { 
            box-shadow: 0 0 0 0 rgba(251, 146, 60, 0.4);
        }
        50% { 
            box-shadow: 0 0 0 4px rgba(251, 146, 60, 0.2);
        }
    }

    /* Campos vazios OBRIGATÓRIOS - classe aplicada via data-testid */
    [data-testid="stTextInput"][data-empty="required"] > div > div > input,
    [data-testid="stSelectbox"][data-empty="required"] > div > div > select {
        border: 2.5px solid #FB923C !important;
        background: linear-gradient(135deg, #FFFBEB 0%, #FEF3C7 100%) !important;
        animation: glow-pulse 2s infinite !important;
    }

    [data-testid="stTextInput"][data-empty="required"] > div > div > input:focus,
    [data-testid="stSelectbox"][data-empty="required"] > div > div > select:focus {
        border-color: #F97316 !important;
        box-shadow: 0 0 0 4px rgba(251, 146, 60, 0.25) !important;
    }

    /* Campos vazios RECOMENDADOS */
    [data-testid="stTextInput"][data-empty="recommended"] > div > div > input,
    [data-testid="stSelectbox"][data-empty="recommended"] > div > div > select {
        border: 2px solid #60A5FA !important;
        background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%) !important;
    }

    [data-testid="stTextInput"][data-empty="recommended"] > div > div > input:focus,
    [data-testid="stSelectbox"][data-empty="recommended"] > div > div > select:focus {
        border-color: #3B82F6 !important;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2) !important;
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
    logo_html = ""

    if os.path.exists(CFG.LOGO_PATH):
        try:
            if 'logo_b64' not in st.session_state:
                with open(CFG.LOGO_PATH, "rb") as f:
                    st.session_state.logo_b64 = base64.b64encode(f.read()).decode()

            logo_html = f'<img src="data:image/jpeg;base64,{st.session_state.logo_b64}" style="width:56px;height:56px;object-fit:contain;border-radius:12px;background:rgba(255,255,255,.15);padding:6px;" />'
        except Exception as e:
            logger.warning(f"Erro ao carregar logo: {e}")

    header_html = f"""
    <div style="background:linear-gradient(135deg,#1D4ED8,#0B3AA8);
                color:white;border-radius:18px;padding:16px 18px;
                box-shadow:0 10px 20px rgba(2,6,23,.08);margin-bottom:18px;
                display:flex;align-items:center;gap:12px;">
        {logo_html}
        <div>
            <h1 style="margin:0;font-size:1.25rem;font-weight:900;line-height:1.1;">
                {title} <span style="font-size:0.7rem;opacity:0.8;">v{CFG.VERSION}</span>
            </h1>
            <p style="margin:0;opacity:.95;font-weight:650;">
                Atualização cadastral de membros
            </p>
        </div>
    </div>
    """

    st.markdown(header_html, unsafe_allow_html=True)

def render_card_header(title: str, subtitle: str = ""):
    subtitle_html = f'<div style="color:#475569;font-weight:650;">{subtitle}</div>' if subtitle else ''

    html = f"""
    <div style="background:white;border:2px solid #DBEAFE;border-radius:18px;
                padding:18px;box-shadow:0 10px 20px rgba(2,6,23,.08);margin:14px 0;">
        <div style="font-weight:900;color:#0B3AA8;font-size:1.15rem;margin-bottom:10px;">
            {title}
        </div>
        {subtitle_html}
    </div>
    """

    height = 100 if subtitle else 70
    components.html(html, height=height)

def render_member_preview(member: dict, total_found: int):
    import html

    nome = html.escape(TextUtils.clean(member.get("nome_completo", "")) or "(Sem nome)")
    cong = html.escape(TextUtils.clean(member.get("congregacao", "")) or "sem informação")
    mae = html.escape(TextUtils.clean(member.get("nome_mae", "")))
    dn = Formatters.parse_date(member.get("data_nasc"))
    data_str = html.escape(Formatters.date_br(dn))

    html_content = f"""
    <style>
        .card {{
            background: white;
            border: 2px solid #DBEAFE;
            border-radius: 18px;
            padding: 18px;
            box-shadow: 0 10px 20px rgba(2, 6, 23, .08);
            margin: 14px 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', 'Roboto', sans-serif;
        }}

        .section {{
            font-weight: 800;
            color: #0B3AA8;
            font-size: 1.25rem;
            margin-bottom: 8px;
            letter-spacing: -0.02em;
            line-height: 1.3;
        }}

        .small {{
            color: #64748B;
            font-weight: 600;
            font-size: 0.95rem;
            line-height: 1.5;
        }}

        .found-name {{
            margin-top: 16px;
            font-weight: 800;
            color: #0B3AA8;
            font-size: 1.35rem;
            line-height: 1.3;
            letter-spacing: -0.02em;
        }}

        .cong-muted {{
            margin-top: 8px;
            font-size: 0.95rem;
            font-weight: 600;
            color: #64748B;
            line-height: 1.5;
        }}

        .info-label {{
            font-size: 0.8rem;
            font-weight: 700;
            color: #64748B;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 4px;
        }}

        .info-value {{
            font-size: 1.1rem;
            font-weight: 700;
            color: #0B3AA8;
            line-height: 1.4;
            letter-spacing: -0.01em;
        }}
    </style>

    <div class="card">
        <div class="section">Cadastro encontrado</div>
        <div class="small">Achamos {total_found} registro(s). Selecione e atualize.</div>

        <div style="margin-top:16px;">
            <div class="info-label"><b>Data de nascimento</b></div>
            <div class="info-value">{data_str}</div>

            <div style="margin-top:14px;">
                <div class="info-label"><b>Nome da mãe</b></div>
                <div class="info-value">{mae}</div>
            </div>

            <div class="found-name">{nome}</div>
            <div class="cong-muted">Congregação: {cong}</div>
        </div>
    </div>
    """

    components.html(html_content, height=280)

# ============================================================================
# FORMULÁRIO COM DESTAQUE VISUAL INTEGRADO
# ============================================================================

def render_member_form(
    mode: Literal["new", "edit"],
    initial_data: Optional[dict] = None,
    dropdown_opts: Optional[dict] = None
) -> Optional[dict]:
    """Formulário com campos vazios destacados visualmente"""

    initial_data = initial_data or {}
    dropdown_opts = dropdown_opts or {}

    key_prefix = f"{mode}_"
    birth_date = Formatters.parse_date(initial_data.get("data_nasc"))

    # Verifica campos vazios
    empty_fields = {
        'cpf': TextUtils.is_empty(initial_data.get("cpf", "")),
        'whatsapp': TextUtils.is_empty(initial_data.get("whatsapp_telefone", "")),
        'endereco': TextUtils.is_empty(initial_data.get("endereco", "")),
        'bairro': TextUtils.is_empty(initial_data.get("bairro_distrito", "")),
        'pai': TextUtils.is_empty(initial_data.get("nome_pai", "")),
        'naturalidade': TextUtils.is_empty(initial_data.get("naturalidade", "")),
        'nacionalidade': TextUtils.is_empty(initial_data.get("nacionalidade", "")),
        'estado_civil': TextUtils.is_empty(initial_data.get("estado_civil", "")),
        'batismo': TextUtils.is_empty(initial_data.get("data_batismo", "")),
        'congregacao': TextUtils.is_empty(initial_data.get("congregacao", "")),
    }

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
            cpf_label = "⚠️ CPF * (campo vazio)" if empty_fields['cpf'] else "CPF *"
            cpf_input = st.text_input(
                cpf_label,
                value=Formatters.cpf(initial_data.get("cpf", "")),
                placeholder="000.000.000-00",
                key=f"{key_prefix}cpf",
                max_chars=14,
                help="Campo obrigatório - preencher" if empty_fields['cpf'] else None
            )
            # Aplica classe CSS via JavaScript
            if empty_fields['cpf']:
                st.markdown("""
                <script>
                var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
                inputs[inputs.length-1].setAttribute('data-empty', 'required');
                </script>
                """, unsafe_allow_html=True)

        whats_label = "⚠️ WhatsApp/Telefone * (campo vazio)" if empty_fields['whatsapp'] else "WhatsApp/Telefone *"
        whats_input = st.text_input(
            whats_label,
            value=Formatters.phone(initial_data.get("whatsapp_telefone", "")),
            placeholder="(88) 9.9999-9999",
            key=f"{key_prefix}whats",
            max_chars=15,
            help="Campo obrigatório - preencher" if empty_fields['whatsapp'] else None
        )
        if empty_fields['whatsapp']:
            st.markdown("""
            <script>
            var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
            inputs[inputs.length-1].setAttribute('data-empty', 'required');
            </script>
            """, unsafe_allow_html=True)

        st.markdown("### 📍 Endereço")

        bairro_current = TextUtils.clean(initial_data.get("bairro_distrito", ""))
        bairro_idx = CFG.BAIRROS.index(bairro_current) if bairro_current in CFG.BAIRROS else 0

        bairro_label = "⚠️ Bairro/Distrito * (campo vazio)" if empty_fields['bairro'] else "Bairro/Distrito *"
        bairro = st.selectbox(
            bairro_label,
            options=CFG.BAIRROS,
            index=bairro_idx,
            key=f"{key_prefix}bairro",
            help="Campo obrigatório - selecionar" if empty_fields['bairro'] else None
        )
        if empty_fields['bairro']:
            st.markdown("""
            <script>
            var selects = window.parent.document.querySelectorAll('[data-testid="stSelectbox"]');
            selects[selects.length-1].setAttribute('data-empty', 'required');
            </script>
            """, unsafe_allow_html=True)

        endereco_label = "⚠️ Endereço completo * (campo vazio)" if empty_fields['endereco'] else "Endereço completo *"
        endereco = st.text_input(
            endereco_label,
            value=TextUtils.clean(initial_data.get("endereco", "")),
            key=f"{key_prefix}endereco",
            placeholder="Rua, número, complemento",
            help="Campo obrigatório - preencher" if empty_fields['endereco'] else None
        )
        if empty_fields['endereco']:
            st.markdown("""
            <script>
            var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
            inputs[inputs.length-1].setAttribute('data-empty', 'required');
            </script>
            """, unsafe_allow_html=True)

        st.markdown("### 👨‍👩‍👧 Filiação")

        col1, col2 = st.columns(2)
        with col1:
            mae = st.text_input(
                "Nome da mãe *",
                value=TextUtils.clean(initial_data.get("nome_mae", "")),
                key=f"{key_prefix}mae"
            )

        with col2:
            pai_label = "💡 Nome do pai (recomendado)" if empty_fields['pai'] else "Nome do pai"
            pai = st.text_input(
                pai_label,
                value=TextUtils.clean(initial_data.get("nome_pai", "")),
                key=f"{key_prefix}pai",
                help="Recomendado preencher" if empty_fields['pai'] else None
            )
            if empty_fields['pai']:
                st.markdown("""
                <script>
                var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
                inputs[inputs.length-1].setAttribute('data-empty', 'recommended');
                </script>
                """, unsafe_allow_html=True)

        st.markdown("### 📝 Dados complementares")

        col1, col2 = st.columns(2)
        with col1:
            nat_label = "💡 Naturalidade (recomendado)" if empty_fields['naturalidade'] else "Naturalidade"
            naturalidade = st.text_input(
                nat_label,
                value=TextUtils.clean(initial_data.get("naturalidade", "")),
                key=f"{key_prefix}nat",
                placeholder="Cidade de nascimento",
                help="Recomendado preencher" if empty_fields['naturalidade'] else None
            )
            if empty_fields['naturalidade']:
                st.markdown("""
                <script>
                var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
                inputs[inputs.length-1].setAttribute('data-empty', 'recommended');
                </script>
                """, unsafe_allow_html=True)

        with col2:
            nac_opts = dropdown_opts.get("nacionalidade", ["BRASILEIRA", "BRASILEIRO", "OUTRA"])
            nac_current = TextUtils.clean(initial_data.get("nacionalidade", "")).upper()
            nac_idx = nac_opts.index(nac_current) if nac_current in nac_opts else 0

            nac_label = "💡 Nacionalidade (recomendado)" if empty_fields['nacionalidade'] else "Nacionalidade"
            nacionalidade = st.selectbox(
                nac_label,
                options=nac_opts,
                index=nac_idx,
                key=f"{key_prefix}nac",
                help="Recomendado preencher" if empty_fields['nacionalidade'] else None
            )
            if empty_fields['nacionalidade']:
                st.markdown("""
                <script>
                var selects = window.parent.document.querySelectorAll('[data-testid="stSelectbox"]');
                selects[selects.length-1].setAttribute('data-empty', 'recommended');
                </script>
                """, unsafe_allow_html=True)

        ec_opts = dropdown_opts.get("estado_civil", [e.value for e in EstadoCivil])
        ec_current = TextUtils.clean(initial_data.get("estado_civil", "")).upper()
        ec_idx = ec_opts.index(ec_current) if ec_current in ec_opts else 0

        ec_label = "⚠️ Estado civil * (campo vazio)" if empty_fields['estado_civil'] else "Estado civil *"
        estado_civil = st.selectbox(
            ec_label,
            options=ec_opts,
            index=ec_idx,
            key=f"{key_prefix}ec",
            help="Campo obrigatório - selecionar" if empty_fields['estado_civil'] else None
        )
        if empty_fields['estado_civil']:
            st.markdown("""
            <script>
            var selects = window.parent.document.querySelectorAll('[data-testid="stSelectbox"]');
            selects[selects.length-1].setAttribute('data-empty', 'required');
            </script>
            """, unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        with col1:
            bat_label = "💡 Data do batismo (recomendado)" if empty_fields['batismo'] else "Data do batismo"
            batismo = st.text_input(
                bat_label,
                value=TextUtils.clean(initial_data.get("data_batismo", "")),
                key=f"{key_prefix}bat",
                placeholder="Ex.: 05/12/1992",
                help="Recomendado preencher" if empty_fields['batismo'] else None
            )
            if empty_fields['batismo']:
                st.markdown("""
                <script>
                var inputs = window.parent.document.querySelectorAll('[data-testid="stTextInput"]');
                inputs[inputs.length-1].setAttribute('data-empty', 'recommended');
                </script>
                """, unsafe_allow_html=True)

        with col2:
            cong_opts = dropdown_opts.get("congregacao", ["SEDE", "OUTRA"])
            cong_current = TextUtils.clean(initial_data.get("congregacao", "")).upper()
            cong_idx = cong_opts.index(cong_current) if cong_current in cong_opts else 0

            cong_label = "⚠️ Congregação * (campo vazio)" if empty_fields['congregacao'] else "Congregação *"
            congregacao = st.selectbox(
                cong_label,
                options=cong_opts,
                index=cong_idx,
                key=f"{key_prefix}cong",
                help="Campo obrigatório - selecionar" if empty_fields['congregacao'] else None
            )
            if empty_fields['congregacao']:
                st.markdown("""
                <script>
                var selects = window.parent.document.querySelectorAll('[data-testid="stSelectbox"]');
                selects[selects.length-1].setAttribute('data-empty', 'required');
                </script>
                """, unsafe_allow_html=True)

        st.divider()

        # Resumo compacto
        total_empty_required = sum([
            empty_fields['cpf'], empty_fields['whatsapp'], 
            empty_fields['endereco'], empty_fields['bairro'],
            empty_fields['estado_civil'], empty_fields['congregacao']
        ])

        total_empty_recommended = sum([
            empty_fields['pai'], empty_fields['naturalidade'],
            empty_fields['nacionalidade'], empty_fields['batismo']
        ])

        if total_empty_required > 0 or total_empty_recommended > 0:
            col_a, col_b = st.columns(2)

            with col_a:
                if total_empty_required > 0:
                    st.warning(f"⚠️ {total_empty_required} campo(s) obrigatório(s) vazio(s)", icon="⚠️")

            with col_b:
                if total_empty_recommended > 0:
                    st.info(f"💡 {total_empty_recommended} campo(s) recomendado(s) vazio(s)", icon="💡")

        st.caption("**Campos marcados com * são obrigatórios**")

        submit_label = "✓ Salvar novo cadastro" if mode == "new" else "✓ Salvar atualização"
        submitted = st.form_submit_button(submit_label, use_container_width=True)

        if submitted:
            return {
                "nome_completo": TextUtils.sanitize_input(nome),
                "data_nasc": data_nasc,
                "cpf": cpf_input,
                "whatsapp_telefone": whats_input,
                "bairro_distrito": bairro,
                "endereco": TextUtils.sanitize_input(endereco),
                "nome_mae": TextUtils.sanitize_input(mae),
                "nome_pai": TextUtils.sanitize_input(pai),
                "nacionalidade": nacionalidade,
                "naturalidade": TextUtils.sanitize_input(naturalidade),
                "estado_civil": estado_civil,
                "data_batismo": TextUtils.sanitize_input(batismo),
                "congregacao": congregacao,
            }

        return None

# ============================================================================
# MAIN APP
# ============================================================================

def initialize_session():
    defaults = {
        "searched": False,
        "match_ids": [],
        "search_dn": None,
        "search_mae": "",
        "_cached_df": None,
        "last_update": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

@measure_time
def main():
    st.set_page_config(
        page_title=CFG.TITLE,
        page_icon=CFG.ICON,
        layout="centered",
        initial_sidebar_state="collapsed"
    )

    render_css()
    render_header(CFG.TITLE)

    initialize_session()

    sheets = SheetsService()
    worksheet = sheets.get_worksheet()

    if not worksheet:
        st.stop()

    with st.spinner("🔄 Carregando base de dados..."):
        df = SheetsService.load_dataframe(worksheet)
        st.session_state._cached_df = df

    if df.empty:
        st.error("❌ Não foi possível carregar os dados")
        logger.error("DataFrame vazio")
        st.stop()

    df_hash = hashlib.md5(str(df.shape).encode()).hexdigest()

    dropdown_opts = {
        "nacionalidade": build_dropdown_options(df_hash, "nacionalidade"),
        "estado_civil": build_dropdown_options(df_hash, "estado_civil"),
        "congregacao": build_dropdown_options(df_hash, "congregacao"),
    }

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
            help="Insira o nome completo"
        )

    @rate_limit(max_calls=CFG.RATE_LIMIT_CALLS, time_window=CFG.RATE_LIMIT_WINDOW)
    def perform_search():
        if not input_date:
            st.warning("⚠️ Escolha a data de nascimento")
            return False

        if not input_mother.strip() or len(input_mother.strip()) < 2:
            st.warning("⚠️ Digite pelo menos 2 letras do nome da mãe")
            return False

        with st.spinner("🔎 Buscando..."):
            matches = find_members(df, input_date, input_mother)

            st.session_state.searched = True
            st.session_state.search_dn = input_date
            st.session_state.search_mae = input_mother.strip()
            st.session_state.match_ids = matches.index.tolist()

        return True

    if st.button("🔍 Buscar cadastro", use_container_width=True):
        if perform_search():
            st.rerun()

    if not st.session_state.searched:
        st.stop()

    st.divider()

    match_ids = st.session_state.match_ids

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
            is_valid, errors = validate_member_data(form_data)

            if not is_valid:
                for err in errors:
                    st.error(f"❌ {err}")
                st.stop()

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

            with st.spinner("💾 Salvando..."):
                if SheetsService.append_row(worksheet, payload):
                    st.success(f"✅ Cadastro salvo! ID: {new_id}")
                    st.balloons()

                    sleep(2)

                    st.session_state.searched = False
                    st.session_state.match_ids = []
                    st.session_state.search_dn = None
                    st.session_state.search_mae = ""
                    st.session_state.last_update = datetime.now(CFG.TZ)
                    st.rerun()

        st.stop()

    matches_df = df.loc[match_ids].copy()
    total_found = len(matches_df)

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

    row_data = df.loc[selected_idx].to_dict()
    render_member_preview(row_data, total_found)

    sheet_row = int(row_data["_sheet_row"])

    form_data = render_member_form(
        mode="edit",
        initial_data=row_data,
        dropdown_opts=dropdown_opts
    )

    if form_data:
        is_valid, errors = validate_member_data(form_data)

        if not is_valid:
            for err in errors:
                st.error(f"❌ {err}")
            st.stop()

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

        with st.spinner("💾 Salvando alterações..."):
            if SheetsService.update_row(worksheet, sheet_row, payload):
                st.success("✅ Cadastro atualizado com sucesso!")
                st.balloons()

                sleep(2)

                st.session_state.searched = False
                st.session_state.match_ids = []
                st.session_state.search_dn = None
                st.session_state.search_mae = ""
                st.session_state.last_update = datetime.now(CFG.TZ)
                st.rerun()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Erro fatal na aplicação")
        st.error(f"❌ Erro inesperado: {str(e)}")
        st.error("Tente recarregar a página. Se o problema persistir, entre em contato.")
