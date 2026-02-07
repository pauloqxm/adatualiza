# 🚀 GUIA RÁPIDO: Enviar para AWS

## ✅ PASSO A PASSO COMPLETO

### 1️⃣ CRIAR SERVIDOR NA AWS (10 minutos)

1. Acesse: https://console.aws.amazon.com/ec2
2. Clique em **"Launch Instance"** (botão laranja)
3. Configure:
   - **Nome**: `adatualiza-app`
   - **AMI**: Selecione **"Amazon Linux 2023"**
   - **Instance type**: Selecione **"t3.small"**
   - **Key pair**: 
     - Clique em "Create new key pair"
     - Nome: `adatualiza-key`
     - Tipo: RSA
     - Formato: .pem
     - **BAIXE E GUARDE O ARQUIVO .pem**
   - **Network settings**: Clique em "Edit"
     - Marque: ✅ Allow SSH traffic from (Anywhere)
     - Clique em "Add security group rule"
     - Type: Custom TCP
     - Port: 8501
     - Source: Anywhere (0.0.0.0/0)
4. Clique em **"Launch instance"**
5. Aguarde 2 minutos
6. Clique na instância criada
7. **COPIE O "Public IPv4 address"** (ex: 54.123.45.67)

---

### 2️⃣ TRANSFERIR ARQUIVOS (5 minutos)

#### Opção A: Usando WinSCP (MAIS FÁCIL) ⭐

1. **Baixe WinSCP**: https://winscp.net/eng/download.php
2. **Instale e abra**
3. **Configure conexão**:
   - File protocol: `SFTP`
   - Host name: `SEU-IP-PUBLICO` (o que você copiou)
   - Port: `22`
   - User name: `ec2-user`
   - Password: (deixe vazio)
   - Clique em **"Advanced"**
   - SSH → Authentication → Private key file
   - Selecione seu arquivo `.pem`
   - Clique em **"OK"**
4. Clique em **"Login"**
5. **Arraste a pasta** `C:\Users\paulo.ferreira\Github\adatualiza` para o lado direito (servidor)

#### Opção B: Usando PowerShell

```powershell
# Substitua pelos seus valores
$IP = "SEU-IP-PUBLICO"
$KEY = "C:\Users\paulo.ferreira\Downloads\adatualiza-key.pem"

# Transferir arquivos
scp -i $KEY -r C:\Users\paulo.ferreira\Github\adatualiza ec2-user@${IP}:/home/ec2-user/
```

---

### 3️⃣ CONFIGURAR SERVIDOR (5 minutos)

1. **Conectar via SSH**:

```powershell
# No PowerShell
ssh -i C:\Users\paulo.ferreira\Downloads\adatualiza-key.pem ec2-user@SEU-IP-PUBLICO
```

2. **Instalar Python e dependências**:

```bash
# Atualizar sistema
sudo yum update -y

# Instalar Python 3.11
sudo yum install python3.11 python3.11-pip -y

# Entrar na pasta
cd adatualiza

# Criar ambiente virtual
python3.11 -m venv venv

# Ativar ambiente
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
```

3. **Configurar credenciais do Google**:

```bash
# Criar arquivo de secrets
mkdir -p .streamlit
nano .streamlit/secrets.toml
```

**Cole suas credenciais** (pegue do seu arquivo local `.streamlit/secrets.toml`):
```toml
[gcp_service_account]
type = "service_account"
project_id = "seu-projeto"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

**Salvar**: Pressione `Ctrl+X`, depois `Y`, depois `Enter`

---

### 4️⃣ INICIAR APLICAÇÃO (2 minutos)

```bash
# Rodar aplicação
streamlit run adatualiza/app.py --server.port=8501 --server.address=0.0.0.0
```

**Acesse no navegador**: `http://SEU-IP-PUBLICO:8501`

---

### 5️⃣ MANTER RODANDO SEMPRE (3 minutos)

Para a aplicação continuar rodando mesmo depois de fechar o terminal:

```bash
# Pressione Ctrl+C para parar o Streamlit

# Criar serviço
sudo nano /etc/systemd/system/adatualiza.service
```

**Cole**:
```ini
[Unit]
Description=Adatualiza Streamlit App
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/adatualiza
Environment="PATH=/home/ec2-user/adatualiza/venv/bin"
ExecStart=/home/ec2-user/adatualiza/venv/bin/streamlit run adatualiza/app.py --server.port=8501 --server.address=0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
```

**Salvar**: `Ctrl+X`, `Y`, `Enter`

**Ativar**:
```bash
sudo systemctl daemon-reload
sudo systemctl enable adatualiza
sudo systemctl start adatualiza
```

**Pronto!** Agora pode fechar o terminal. A aplicação continuará rodando.

---

## 🎯 RESUMO DOS COMANDOS

```bash
# 1. Conectar
ssh -i sua-chave.pem ec2-user@SEU-IP

# 2. Configurar
cd adatualiza
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Criar secrets
mkdir -p .streamlit
nano .streamlit/secrets.toml
# (cole as credenciais)

# 4. Criar serviço
sudo nano /etc/systemd/system/adatualiza.service
# (cole a configuração)

# 5. Iniciar
sudo systemctl daemon-reload
sudo systemctl enable adatualiza
sudo systemctl start adatualiza
```

---

## 📱 ACESSAR A APLICAÇÃO

Abra no navegador: **`http://SEU-IP-PUBLICO:8501`**

---

## 🔧 COMANDOS ÚTEIS

```bash
# Ver logs
sudo journalctl -u adatualiza -f

# Reiniciar
sudo systemctl restart adatualiza

# Parar
sudo systemctl stop adatualiza

# Status
sudo systemctl status adatualiza
```

---

## 💰 CUSTO

- **EC2 t3.small**: ~$15-20/mês
- **Tráfego**: ~$1-5/mês
- **Total**: ~$16-25/mês

---

## ❓ PROBLEMAS COMUNS

### Não consigo conectar via SSH
- Verifique se o Security Group permite SSH (porta 22)
- Verifique se está usando o arquivo .pem correto
- No Windows, use PowerShell (não CMD)

### Aplicação não abre no navegador
- Verifique se o Security Group permite porta 8501
- Confirme que o serviço está rodando: `sudo systemctl status adatualiza`
- Veja os logs: `sudo journalctl -u adatualiza -f`

### Erro de credenciais do Google
- Verifique se copiou todo o conteúdo do secrets.toml
- Confirme que as quebras de linha estão corretas no private_key
