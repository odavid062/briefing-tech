# Briefing Tech Matinal

Bot que todo dia às 8h coleta notícias de tecnologia via RSS, gera um roteiro com GPT-4o-mini, sintetiza voz com edge-tts e entrega no WhatsApp como **mensagem de voz** + digest em texto com links.

**Pipeline:** `cron → RSS → gpt-4o-mini → edge-tts → ffmpeg (opus) → Evolution API → WhatsApp`

### Stack

- **Linguagem & Runtime:** Python 3.12
- **Coleta RSS:** `feedparser`
- **Roteirização & Digest:** OpenAI API (`gpt-4o-mini`) via `requests`
- **Síntese de Voz:** `edge-tts` (Microsoft Edge TTS)
- **Áudio & Codec:** `ffmpeg` (`loudnorm`, codificação OGG Opus mono 48 kHz para mensagem de voz WhatsApp)
- **Envio WhatsApp:** Evolution API v2 (`message/sendWhatsAppAudio` e `message/sendText`)
- **Containerização:** Docker

---

## Setup

### 1. Pré-requisitos

- Docker instalado na máquina/VPS (recomendado) ou Python 3.12+ com `ffmpeg` para execução local
- Evolution API v2 rodando com uma instância conectada ao WhatsApp
- Chave de API da OpenAI

### 2. Configurar o `.env`

```bash
cp .env.example .env
# edite o .env com suas credenciais
```

Variáveis obrigatórias:

| Variável | Descrição |
|---|---|
| `OPENAI_API_KEY` | Chave da OpenAI |
| `EVOLUTION_BASE_URL` | URL base da Evolution API (sem barra final) |
| `EVOLUTION_INSTANCE` | Nome da instância no Evolution |
| `EVOLUTION_APIKEY` | API key do Evolution |
| `WHATSAPP_NUMBER` | Número destino (DDI+DDD+número, só dígitos) |

### 3. Build da imagem

```bash
docker build -t briefing-tech .
```

### 4. Teste manual

```bash
docker run --rm --env-file .env briefing-tech
```

O áudio deve chegar como mensagem de voz seguido do digest em texto.

### 5. Execução local (sem Docker)

Caso prefira executar diretamente na máquina sem container:

1. Certifique-se de ter o `ffmpeg` instalado e acessível no `PATH`.
2. Crie e ative um ambiente virtual:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # No Windows: .venv\Scripts\activate
   ```
3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
4. Execute o script com as variáveis configuradas no `.env`:
   ```bash
   python briefing.py
   ```

---

## Crontab (agendamento)

> **Atenção ao fuso:** verifique o fuso do host com `timedatectl` antes de configurar.
> O container roda em `America/Sao_Paulo`, mas o cron usa o fuso do **host**.
>
> - Host em **UTC**: `0 11 * * *` (11h UTC = 8h Brasília)
> - Host em **America/Sao_Paulo**: `0 8 * * *`

Instalar a entrada de cron (exemplo com host em UTC):

```bash
(crontab -l 2>/dev/null; echo "0 11 * * * docker run --rm --env-file /caminho/absoluto/.env briefing-tech >> /var/log/briefing.log 2>&1") | crontab -
```

Verificar:

```bash
crontab -l
tail -f /var/log/briefing.log
```

---

## Personalização

| Variável | Padrão | Descrição |
|---|---|---|
| `FEEDS` | 6 feeds padrão | Lista separada por vírgula de URLs RSS personalizadas |
| `TTS_VOICE` | `pt-BR-AntonioNeural` | Voz do edge-tts. Liste todas: `edge-tts --list-voices` |
| `TTS_RATE` | `+8%` | Velocidade da fala. Ex: `-5%` para mais devagar, `+15%` para mais rápido |
| `VINHETA` | _(desativado)_ | Caminho absoluto para um MP3 de abertura (montado via volume no Docker) |
| `MAX_ITENS` | `35` | Máximo de notícias coletadas antes de passar para o LLM |
| `JANELA_HORAS` | `26` | Janela de tempo para considerar notícias como recentes |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo OpenAI utilizado |

**Exemplo com vinheta:**

```bash
docker run --rm \
  --env-file .env \
  -e VINHETA=/app/vinheta.mp3 \
  -v /caminho/local/vinheta.mp3:/app/vinheta.mp3:ro \
  briefing-tech
```

Feeds padrão utilizados quando `FEEDS` não é configurada:
- `https://hnrss.org/frontpage` (Hacker News)
- `https://techcrunch.com/feed/` (TechCrunch)
- `https://www.theverge.com/rss/index.xml` (The Verge)
- `https://feeds.arstechnica.com/arstechnica/index` (Ars Technica)
- `https://tecnoblog.net/feed/` (Tecnoblog)
- `https://canaltech.com.br/rss/` (Canaltech)

---

## Estrutura do Projeto

```text
.
├── briefing.py       # Script principal (coleta RSS, prompt/LLM, TTS, conversão ffmpeg e envio WhatsApp)
├── Dockerfile        # Imagem Docker baseada em python:3.12-slim com ffmpeg e tzdata
├── requirements.txt  # Dependências Python (feedparser, edge-tts, requests, python-dotenv)
├── .env.example      # Modelo com variáveis de ambiente necessárias e opcionais
├── .gitignore        # Arquivos ignorados pelo Git (.env, caches, áudios, etc.)
└── README.md         # Documentação do projeto
```

---

## Troubleshooting

### Evolution API v1 vs v2

Este bot usa a API v2 (endpoints `message/sendWhatsAppAudio` e `message/sendText`).

Se sua instância for **v1**, os bodies são diferentes:

```json
// v1 — áudio
{ "audioMessage": { "number": "...", "audio": "<base64>", "delay": 1000 } }

// v1 — texto
{ "textMessage": { "number": "...", "text": "..." } }
```

Ajuste a função `_evo_post` em `briefing.py` conforme a versão. Para identificar, verifique a documentação da sua instância ou teste com curl e observe o response body.

### Validar o ogg com ffprobe

```bash
ffprobe briefing.ogg
# deve exibir: Audio: opus, 48000 Hz, mono
```

### O áudio chega como arquivo, não como mensagem de voz

Confirme que está usando o endpoint `sendWhatsAppAudio` (v2) ou `audioMessage` com o campo correto. A chave que instrui o WhatsApp a tratar como PTT é definida pela Evolution API, não pelo codec.

### Gateway alternativo

Se não usar Evolution API, qualquer gateway que aceite base64 de ogg/opus funciona. Ajuste apenas a função `enviar_whatsapp` e os campos do `.env`. O pipeline de áudio (edge-tts + ffmpeg) permanece igual.

### Container não respeita o fuso horário

Confirme que a imagem foi construída com `TZ=America/Sao_Paulo` (já no Dockerfile). Para testar:

```bash
docker run --rm briefing-tech python -c "from datetime import datetime; print(datetime.now())"
```

### Notificação automática de falha

Em caso de exceção durante a execução, o bloco `if __name__ == "__main__":` do script tenta enviar automaticamente um alerta via Evolution API (`message/sendText`) para o número configurado:
`⚠️ Briefing de hoje falhou: <mensagem de erro>`

---

## TODO — Backlog v2

- [x] Vinheta de abertura (suporte implementado via variável `VINHETA` e mesclagem/normalização no `ffmpeg`)
- [ ] Envio para grupo/canal do WhatsApp → briefing vira produto de audiência
- [ ] Score de relevância por fonte (priorizar sinal alto, despriorizar ruído)
- [ ] Edição de sexta: retrospectiva da semana em vez do diário
