#!/usr/bin/env python3
"""
Converte os documentos das entregas de Markdown para PDF.

    python ferramentas/md_para_pdf.py docs/Entrega_4_Resposta.md

Gera o PDF ao lado do .md, com o mesmo nome. O Markdown continua sendo a
fonte -- edite o .md e rode isto de novo; nunca edite o PDF.

Este script mora em ferramentas/ e nao em docs/ porque docs/ nao e versionado
(os documentos trazem dados pessoais dos integrantes). O conversor e codigo do
projeto e precisa ficar disponivel para o grupo todo.

Dependencias (nao entram no requirements.txt do servidor, sao so para gerar
documento):

    uv pip install --python .venv-evidencias markdown weasyprint

Por que WeasyPrint e nao o Chrome: ele implementa CSS paginado de verdade
(@page), o que permite numerar as paginas, repetir o cabecalho das tabelas que
atravessam a quebra e evitar que um bloco de codigo seja cortado no meio.
"""

import re
import sys
from pathlib import Path

import markdown
from weasyprint import CSS, HTML

# ---------------------------------------------------------------------------
# Diagramas
# ---------------------------------------------------------------------------
# Os diagramas do documento sao desenhados com caracteres de caixa (U+2500 em
# diante), que ficam otimos no GitHub. No PDF eles quebram: as fontes
# monoespacadas deste sistema nao trazem os glifos de canto, entao eles saem
# vazios e a linha horizontal aparece meio caractere deslocada das verticais --
# a caixa fica com um degrau em cada esquina.
#
# A saida e converter para ASCII puro na hora de gerar o PDF. "+", "-", "|" e
# "v" existem em qualquer fonte e com largura identica, entao o alinhamento e
# garantido. O Markdown continua com os caracteres bonitos: quem le no GitHub
# ve a caixa desenhada, quem le o PDF ve a caixa alinhada.

CAIXA_PARA_ASCII = str.maketrans({
    "\u250c": "+", "\u2510": "+", "\u2514": "+", "\u2518": "+",   # cantos
    "\u251c": "+", "\u2524": "+", "\u252c": "+", "\u2534": "+",   # tes
    "\u253c": "+",                                                 # cruz
    "\u2500": "-", "\u2502": "|",                                  # linhas
    "\u25bc": "v", "\u25b2": "^",                                  # setas
})


def simplificar_diagramas(texto_md):
    """Troca caracteres de caixa por ASCII, somente dentro dos blocos ```."""
    partes = texto_md.split("```")

    # Indices impares sao o conteudo dos blocos de codigo; os pares sao o texto
    # normal, onde os caracteres especiais (— e →) devem ficar como estao.
    for i in range(1, len(partes), 2):
        partes[i] = partes[i].translate(CAIXA_PARA_ASCII)

    return "```".join(partes)

# ---------------------------------------------------------------------------
# Estilo
# ---------------------------------------------------------------------------
# Lato para o texto (sans humanista, le bem impressa) e DejaVu Sans Mono para
# codigo -- esta ultima e obrigatoria: os diagramas do documento usam
# caracteres de desenho de caixa (box-drawing), que faltam em muitas fontes
# monoespacadas e apareceriam como retangulos vazios.

ESTILO = """
@page {
    size: A4;
    margin: 2cm 2.1cm 1.8cm;

    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: Lato, sans-serif;
        font-size: 8pt;
        color: #8a8f98;
        padding-top: 6mm;
    }
}

/* O cabecalho corrido comeca na segunda pagina: na primeira ele brigaria
   com o titulo do documento. */
@page :first {
    @top-right { content: none; }
}

@page {
    @top-right {
        content: "Entrega 4 \\2014  Monitor de Postura";
        font-family: Lato, sans-serif;
        font-size: 8pt;
        color: #8a8f98;
        padding-bottom: 5mm;
    }
}

html {
    font-family: Lato, "DejaVu Sans", sans-serif;
    font-size: 10.2pt;
    line-height: 1.5;
    color: #1c1f26;
}

/* Evita linha orfa e viuva em todo o documento. */
p, li { orphans: 3; widows: 3; }

/* ----- Capa -----
   O diagrama de arquitetura tem 28 linhas e nao cabe no que sobra da primeira
   pagina depois do titulo e da tabela de integrantes. Em vez de espremer o
   diagrama ate ficar ilegivel, a primeira pagina vira capa: titulo, dados da
   entrega e integrantes. O <hr> que o Markdown coloca logo depois marca o fim
   da capa. */

h1 {
    font-size: 21pt;
    line-height: 1.2;
    margin: 3.2cm 0 3mm;
    color: #0f1115;
    letter-spacing: -0.3pt;
}

body > hr:first-of-type {
    border: none;
    margin: 0;
    break-after: page;
}

h2 {
    font-size: 14pt;
    margin: 7mm 0 3mm;
    padding-top: 2.5mm;
    border-top: 1.2pt solid #dcdfe4;
    color: #0f1115;
    /* Nunca deixa um titulo sozinho no pe da pagina. */
    break-after: avoid;
}

h3 {
    font-size: 11.5pt;
    margin: 6mm 0 2mm;
    color: #2b303a;
    break-after: avoid;
}

p { margin: 0 0 3mm; }

ul, ol { margin: 0 0 3mm; padding-left: 7mm; }
li { margin-bottom: 1.2mm; }

a { color: #1a4f8a; text-decoration: none; }

strong { font-weight: 700; color: #0f1115; }

hr {
    border: none;
    border-top: 1pt solid #e4e7ec;
    margin: 4mm 0;
}

/* Um <hr> seguido de titulo desenhava duas linhas horizontais coladas: a do
   proprio <hr> e a borda superior do <h2>. Fica so a do <hr>. */
hr + h2 {
    border-top: none;
    margin-top: 5mm;
    padding-top: 0;
}

/* ----- Tabelas ----- */

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 8.8pt;
    margin: 0 0 4mm;
    break-inside: auto;
}

/* Se a tabela atravessar a quebra de pagina, o cabecalho se repete. */
thead { display: table-header-group; }

th {
    background: #eef0f4;
    text-align: left;
    font-weight: 700;
    padding: 2mm 2.4mm;
    border: 0.6pt solid #d3d7de;
    color: #2b303a;
}

td {
    padding: 2mm 2.4mm;
    border: 0.6pt solid #d3d7de;
    vertical-align: top;
}

/* Uma linha de tabela nunca e cortada no meio. */
tr { break-inside: avoid; }

/* ----- Codigo ----- */

code {
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 8.4pt;
    background: #f1f3f6;
    padding: 0.3mm 0.5mm;
    border-radius: 0.8mm;
    /* URLs e nomes longos dentro de tabela precisam poder quebrar, senao
       estouram a largura da coluna. */
    overflow-wrap: anywhere;
}

pre {
    background: #f7f8fa;
    border: 0.6pt solid #dde1e7;
    border-left: 2.2pt solid #9aa3b2;
    border-radius: 1mm;
    padding: 3mm 3.5mm;
    margin: 0 0 4mm;
    /* Um bloco de codigo cortado no meio fica ilegivel, sobretudo os
       diagramas. Preferimos empurrar para a proxima pagina. */
    break-inside: avoid;
}

pre code {
    font-size: 7.9pt;
    line-height: 1.3;
    background: none;
    padding: 0;
    border-radius: 0;
    white-space: pre;
    overflow-wrap: normal;
}

/* A lista de metadados do inicio (Projeto, Disciplina, Data, Repositorio). */
h1 + ul {
    list-style: none;
    padding-left: 0;
    margin-bottom: 5mm;
    font-size: 9.6pt;
    color: #4a5059;
}
h1 + ul li { margin-bottom: 0.8mm; }
"""


def converter(caminho_md):
    origem = Path(caminho_md)

    if not origem.exists():
        sys.exit(f"arquivo nao encontrado: {origem}")

    destino = origem.with_suffix(".pdf")

    corpo = markdown.markdown(
        simplificar_diagramas(origem.read_text(encoding="utf-8")),
        extensions=[
            "tables",        # as tabelas do documento
            "fenced_code",   # blocos ``` com os diagramas e o JSON
            "sane_lists",    # nao mistura lista numerada com marcador
            "smarty",        # aspas e travessoes tipograficos
        ],
        output_format="html",
    )

    html = f"""<!doctype html>
<html lang="pt-br">
<head><meta charset="utf-8"><title>{origem.stem}</title></head>
<body>
{corpo}
</body>
</html>"""

    # base_url permite que o HTML referencie imagens relativas a pasta do .md,
    # caso alguma entrega futura inclua figuras.
    HTML(string=html, base_url=str(origem.parent)).write_pdf(
        destino, stylesheets=[CSS(string=ESTILO)]
    )

    print(f"{destino}  ({destino.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(f"uso: python {sys.argv[0]} docs/Entrega_4_Resposta.md")

    for arquivo in sys.argv[1:]:
        converter(arquivo)
