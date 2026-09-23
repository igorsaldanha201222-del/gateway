"""Gera os arquivos de marca do console a partir do logo institucional.

Roda uma vez; o resultado entra no repositório e é empacotado no executável.
O Pillow é dependência só deste script, não do gateway.

    py -3 deploy/windows/gerar_marca.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PROJETO = Path(__file__).resolve().parents[2]      # gridco-gateway
RAIZ = PROJETO.parent                              # a raiz do repositorio
ORIGEM = RAIZ / "hortina_vitesse" / "publicar" / "gc-assets" / "marca" / "logo.png"
DESTINO = PROJETO / "deploy" / "windows"
FUNDO_BANNER = (169, 169, 169)  # --banner do ISA-101, para achatar a transparência


def recorta_conteudo(img: Image.Image) -> Image.Image:
    """Corta a moldura transparente em volta do desenho."""
    caixa = img.getbbox()
    return img.crop(caixa) if caixa else img


def main() -> int:
    if not ORIGEM.is_file():
        # Os arquivos gerados (gridco.ico, gridco-logo.png) estão versionados,
        # então o build não depende deste script. Ele só é necessário quando a
        # marca muda, e aí o logo institucional precisa estar no disco.
        print("logo institucional não encontrado em:", ORIGEM)
        print("Nada a fazer: gridco.ico e gridco-logo.png já estão no repositório.")
        return 0
    logo = Image.open(ORIGEM).convert("RGBA")
    logo = recorta_conteudo(logo)
    print("logo completo:", logo.size)

    # --- símbolo: a parte circular, que é o começo da imagem ---------------
    # O "G" é a região quadrada à esquerda; a largura é a própria altura.
    lado = logo.height
    simbolo = recorta_conteudo(logo.crop((0, 0, lado, lado)))

    # Quadrado com folga, para o ícone não encostar na borda.
    folga = int(max(simbolo.size) * 0.12)
    lado_final = max(simbolo.size) + folga * 2
    quadro = Image.new("RGBA", (lado_final, lado_final), (0, 0, 0, 0))
    quadro.paste(simbolo,
                 ((lado_final - simbolo.width) // 2, (lado_final - simbolo.height) // 2),
                 simbolo)

    ico = DESTINO / "gridco.ico"
    quadro.save(ico, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ícone:", ico, quadro.size)

    # --- logo do banner: PNG já achatado sobre o cinza da faixa -----------
    altura = 26
    largura = round(logo.width * altura / logo.height)
    faixa = logo.resize((largura, altura), Image.LANCZOS)
    achatado = Image.new("RGB", faixa.size, FUNDO_BANNER)
    achatado.paste(faixa, (0, 0), faixa)
    png = DESTINO / "gridco-logo.png"
    achatado.save(png, format="PNG")
    print("logo do banner:", png, achatado.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
