#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Briefing Tech Matinal
RSS → gpt-4o-mini → edge-tts → ffmpeg → WhatsApp (Evolution API)

Roda uma vez por execução. Agende via cron (ver README).
"""

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import edge_tts
import feedparser
import requests
from dotenv import load_dotenv

load_dotenv()

# ------------------------- Config ------------------------- #

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EVO_BASE_URL = os.environ["EVOLUTION_BASE_URL"].rstrip("/")
EVO_INSTANCE = os.environ["EVOLUTION_INSTANCE"]
EVO_APIKEY = os.environ["EVOLUTION_APIKEY"]
WHATS_NUMBER = os.environ["WHATSAPP_NUMBER"]  # ex: 5562999999999

MODELO = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural")
TTS_RATE = os.getenv("TTS_RATE", "+8%")
MAX_ITENS = int(os.getenv("MAX_ITENS", "35"))
JANELA_HORAS = int(os.getenv("JANELA_HORAS", "26"))

FEEDS_PADRAO = [
    "https://hnrss.org/frontpage",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://tecnoblog.net/feed/",
    "https://canaltech.com.br/rss/",
]
FEEDS = [f.strip() for f in os.getenv("FEEDS", "").split(",") if f.strip()] or FEEDS_PADRAO

# ------------------------- Coleta ------------------------- #


def _limpar(texto: str, limite: int = 280) -> str:
    """Remove HTML, colapsa espaços e trunca."""
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(re.sub(r"\s+", " ", texto)).strip()
    return texto[:limite]


def coletar_noticias() -> list[dict]:
    corte = datetime.now(timezone.utc) - timedelta(hours=JANELA_HORAS)
    vistos: set[str] = set()
    itens: list[dict] = []

    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # feedparser raramente lança, mas garantia é garantia
            print(f"[warn] feed falhou: {url} ({e})")
            continue

        fonte = feed.feed.get("title", url)
        for entrada in feed.entries:
            data = entrada.get("published_parsed") or entrada.get("updated_parsed")
            if data and datetime(*data[:6], tzinfo=timezone.utc) < corte:
                continue

            titulo = _limpar(entrada.get("title", ""), 160)
            if not titulo:
                continue

            chave = hashlib.md5(titulo.lower().encode()).hexdigest()
            if chave in vistos:
                continue
            vistos.add(chave)

            itens.append({
                "titulo": titulo,
                "fonte": fonte,
                "resumo": _limpar(entrada.get("summary", "")),
                "link": entrada.get("link", ""),
            })

    return itens[:MAX_ITENS]


# ------------------------- LLM ------------------------- #

PROMPT_SISTEMA = """Você é o editor-chefe de um briefing diário de tecnologia em áudio, \
no estilo de um podcast matinal brasileiro: direto, inteligente, zero enrolação.

A partir das notícias brutas fornecidas, produza DOIS artefatos e responda APENAS com um \
JSON válido no formato {"roteiro": "...", "digest": "..."}.

REGRAS DO ROTEIRO (campo "roteiro"):
- Texto corrido em PT-BR para ser lido por TTS: sem markdown, sem emojis, sem listas, sem URLs.
- Duração alvo de 3 a 4 minutos falados (entre 450 e 600 palavras).
- Abra com uma saudação curta e a data de hoje: __DATA__.
- Agrupe as notícias em 3 a 5 blocos temáticos (ex.: IA, big techs, dev e open source, Brasil, hardware).
- Selecione só o que importa: priorize impacto e novidade, descarte ruído e repetições.
- Em cada bloco, diga em 1 frase POR QUE aquilo importa para quem trabalha com tecnologia.
- Feche com um insight: uma ideia de conteúdo de 2 frases que um dev criador de conteúdo poderia gravar hoje.

REGRAS DO DIGEST (campo "digest"):
- Versão texto para WhatsApp: as 6 a 10 manchetes mais relevantes.
- Formato por item: "• Manchete — Fonte" e, na linha de baixo, o link.
- Use quebras de linha (\\n). Sem markdown além do bullet •."""


def data_em_portugues() -> str:
    agora = datetime.now()  # respeita TZ do container (America/Sao_Paulo)
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return f"{dias[agora.weekday()]}, {agora.day} de {meses[agora.month - 1]} de {agora.year}"


def gerar_textos(noticias: list[dict]) -> dict:
    entrada = "\n".join(
        f"- [{n['fonte']}] {n['titulo']} :: {n['resumo']} :: {n['link']}"
        for n in noticias
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": MODELO,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA.replace("__DATA__", data_em_portugues())},
                {"role": "user", "content": f"Notícias coletadas nas últimas {JANELA_HORAS}h:\n\n{entrada}"},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    dados = json.loads(resp.json()["choices"][0]["message"]["content"])
    if not dados.get("roteiro") or not dados.get("digest"):
        raise ValueError("LLM não retornou roteiro/digest no formato esperado")
    return dados


# ------------------------- Áudio ------------------------- #


def gerar_audio(roteiro: str, pasta: str) -> str:
    mp3 = os.path.join(pasta, "briefing.mp3")

    async def _tts():
        await edge_tts.Communicate(roteiro, voice=VOICE, rate=TTS_RATE).save(mp3)

    asyncio.run(_tts())
    return mp3


def converter_para_ptt(mp3: str, pasta: str) -> str:
    """Converte para ogg/opus mono 48 kHz — formato de mensagem de voz do WhatsApp."""
    ogg = os.path.join(pasta, "briefing.ogg")
    vinheta = os.getenv("VINHETA")

    if vinheta and os.path.exists(vinheta):
        cmd = [
            "ffmpeg", "-y", "-i", vinheta, "-i", mp3,
            "-filter_complex",
            "[0:a]aresample=48000,aformat=channel_layouts=mono[a0];"
            "[1:a]aresample=48000,aformat=channel_layouts=mono[a1];"
            "[a0][a1]concat=n=2:v=0:a=1,loudnorm[a]",
            "-map", "[a]",
            "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1", ogg,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", mp3,
            "-af", "loudnorm",
            "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1", ogg,
        ]

    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.decode(errors='ignore')[-400:]}")
    return ogg


# ------------------------- WhatsApp (Evolution API v2) ------------------------- #


def _evo_post(rota: str, payload: dict) -> dict:
    r = requests.post(
        f"{EVO_BASE_URL}/{rota}/{EVO_INSTANCE}",
        headers={"apikey": EVO_APIKEY, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def enviar_whatsapp(ogg: str, digest: str) -> None:
    with open(ogg, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    # chega como mensagem de voz (ptt)
    _evo_post("message/sendWhatsAppAudio", {
        "number": WHATS_NUMBER,
        "audio": audio_b64,
        "delay": 1000,
    })
    _evo_post("message/sendText", {
        "number": WHATS_NUMBER,
        "text": f"📰 Fontes do briefing de hoje:\n\n{digest}",
    })


# ------------------------- Main ------------------------- #


def main() -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] iniciando briefing…")

    noticias = coletar_noticias()
    if not noticias:
        raise RuntimeError("nenhuma notícia coletada — verifique os feeds")
    print(f"{len(noticias)} notícias coletadas de {len(FEEDS)} feeds")

    textos = gerar_textos(noticias)
    print(f"roteiro gerado: ~{len(textos['roteiro'].split())} palavras")

    with tempfile.TemporaryDirectory() as pasta:
        mp3 = gerar_audio(textos["roteiro"], pasta)
        ogg = converter_para_ptt(mp3, pasta)
        enviar_whatsapp(ogg, textos["digest"])

    print("briefing enviado ✅")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[erro] {e}", file=sys.stderr)
        try:  # best effort: avisa no zap que hoje falhou
            _evo_post("message/sendText", {
                "number": WHATS_NUMBER,
                "text": f"⚠️ Briefing de hoje falhou: {e}",
            })
        except Exception:
            pass
        sys.exit(1)
