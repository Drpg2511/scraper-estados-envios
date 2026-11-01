"""
Corre el scraper contra el portal de demostración y compara la salida con
esperado.csv, envío por envío.

Uso:
    python verificar.py                                   # portal sin fallos
    python verificar.py --error-cada 23 --caducar-tras 110 --lento-prob 0.03 --corridas 5
    python verificar.py --solo-comparar ruta/al/lote_FINAL_....xlsx

Con --corridas N, si el scraper corta el lote se lo vuelve a lanzar sobre el
archivo parcial, hasta N veces, como haría una persona.

Las funciones de este archivo también las usan las pruebas de pruebas/.
"""
import argparse
import csv
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from portal_demo import datos  # noqa: E402

ESPERADO = RAIZ / "esperado.csv"
TRABAJO = RAIZ / "trabajo"


# --- portal -----------------------------------------------------------------
def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Portal:
    """Levanta portal_demo/servidor.py en un proceso aparte mientras dure el bloque `with`."""

    def __init__(self, *opciones, registro_consultas=None):
        self.puerto = puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        self.comando = [sys.executable, str(RAIZ / "portal_demo" / "servidor.py"),
                        "--puerto", str(self.puerto), *map(str, opciones)]
        if registro_consultas:
            self.comando += ["--registro-consultas", str(registro_consultas)]

    def __enter__(self):
        self.proceso = subprocess.Popen(self.comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        limite = time.monotonic() + 15
        while time.monotonic() < limite:
            try:
                urllib.request.urlopen(f"{self.url}/salud", timeout=1)
                return self
            except OSError:
                time.sleep(0.2)
        self.proceso.kill()
        raise RuntimeError("El portal de demostración no arrancó")

    def __exit__(self, *exc):
        self.proceso.terminate()
        self.proceso.wait(timeout=10)


# --- lote y scraper -----------------------------------------------------------
def preparar_carpeta(nombre):
    """Carpeta de trabajo limpia con el Excel de entrada de los 300 envíos."""
    carpeta = TRABAJO / nombre
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    entrada = carpeta / "lote.xlsx"
    datos.escribir_entrada(entrada, datos.generar_envios())
    return carpeta, entrada


def matar_arbol(proceso):
    """Corta el scraper de golpe (como cerrar la ventana), navegador incluido."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proceso.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proceso.kill()
    proceso.wait(timeout=30)


def correr_scraper(entrada, portal, carpeta, matar_tras=None, mostrar=True):
    """
    Ejecuta scraper_demo.py sin ventana sobre `entrada`. Devuelve
    (código de salida, líneas impresas). Con matar_tras=N lo corta de golpe al
    empezar el pedido N+1.
    """
    entorno = dict(os.environ,
                   PORTAL_URL=portal.url,
                   PORTAL_USUARIO=datos.USUARIO_DEMO,
                   PORTAL_CONTRASENA=datos.CONTRASENA_DEMO,
                   ARCHIVO_AVISOS=str(carpeta / "avisos.log"),
                   SLOW_MO_MS="0",
                   PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
    proceso = subprocess.Popen(
        [sys.executable, str(RAIZ / "scraper_demo.py"), "--entrada", str(entrada), "--headless"],
        cwd=carpeta, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")

    lineas, empezados = [], 0
    for linea in proceso.stdout:
        linea = linea.rstrip()
        lineas.append(linea)
        if mostrar and not linea.startswith(("Procesando ID", " -> Estado")):
            print(f"    | {linea}")
        if linea.startswith("Procesando ID"):
            empezados += 1
            if matar_tras is not None and empezados > matar_tras:
                matar_arbol(proceso)
                lineas.append(f"[verificar] scraper cortado de golpe tras {matar_tras} pedidos")
                return None, lineas
    return proceso.wait(), lineas


def ultima_salida(carpeta):
    """El FINAL o PARCIAL más reciente de la carpeta."""
    candidatos = [p for p in carpeta.glob("*.xlsx") if "_FINAL_" in p.name or "_PARCIAL_" in p.name]
    return max(candidatos, key=lambda p: p.stat().st_mtime) if candidatos else None


# --- comparación --------------------------------------------------------------
def leer_esperado():
    if not ESPERADO.exists():
        datos.escribir_esperado(ESPERADO, datos.generar_envios())
    with open(ESPERADO, encoding="utf-8") as f:
        return {fila["ID_Envio"]: fila["Estado_Esperado"] for fila in csv.DictReader(f)}


def comparar(ruta_salida):
    """
    Compara Estado_Scraping con esperado.csv. Un envío puede estar:
    correcto, distinto (estado equivocado) o pendiente (vacío).
    """
    esperado = leer_esperado()
    df = pd.read_excel(ruta_salida, dtype=str)
    obtenidos = dict(zip(df["ID_Envio"], df["Estado_Scraping"].fillna("").str.strip()))
    resultado = {"total": len(esperado), "filas_salida": len(df),
                 "ids_duplicados": int(df["ID_Envio"].duplicated().sum()),
                 "correctos": 0, "distintos": [], "pendientes": [], "faltan_en_salida": []}
    for id_envio, valor in esperado.items():
        if id_envio not in obtenidos:
            resultado["faltan_en_salida"].append(id_envio)
        elif obtenidos[id_envio] == "":
            resultado["pendientes"].append(id_envio)
        elif obtenidos[id_envio] == valor:
            resultado["correctos"] += 1
        else:
            resultado["distintos"].append((id_envio, valor, obtenidos[id_envio]))
    return resultado


def imprimir_reporte(r):
    print(f"  Coinciden:  {r['correctos']} de {r['total']}")
    print(f"  Distintos:  {len(r['distintos'])}")
    print(f"  Pendientes: {len(r['pendientes'])}")
    if r["faltan_en_salida"] or r["ids_duplicados"]:
        print(f"  Filas que faltan en la salida: {len(r['faltan_en_salida'])} · IDs duplicados: {r['ids_duplicados']}")
    for id_envio, esperado, obtenido in r["distintos"][:20]:
        print(f"    {id_envio}: esperado '{esperado}' · obtenido '{obtenido}'")
    if len(r["distintos"]) > 20:
        print(f"    ... y {len(r['distintos']) - 20} más")


def main():
    parser = argparse.ArgumentParser(description="Verifica el scraper contra el portal de demostración.")
    parser.add_argument("--lento-prob", type=float, default=0)
    parser.add_argument("--lento-seg", type=float, default=12)
    parser.add_argument("--error-cada", type=int, default=0)
    parser.add_argument("--caducar-tras", type=int, default=0)
    parser.add_argument("--corridas", type=int, default=1, help="relanzar sobre el parcial hasta N veces")
    parser.add_argument("--solo-comparar", help="no ejecuta nada: compara este Excel con esperado.csv")
    args = parser.parse_args()

    if args.solo_comparar:
        r = comparar(args.solo_comparar)
        imprimir_reporte(r)
        sys.exit(0 if r["correctos"] == r["total"] else 1)

    opciones = ["--lento-prob", args.lento_prob, "--lento-seg", args.lento_seg,
                "--error-cada", args.error_cada, "--caducar-tras", args.caducar_tras]
    carpeta, entrada = preparar_carpeta("verificar")
    with Portal(*opciones) as portal:
        for corrida in range(1, args.corridas + 1):
            print(f"Corrida {corrida}: {entrada.name}")
            codigo, _ = correr_scraper(entrada, portal, carpeta)
            salida = ultima_salida(carpeta)
            if codigo == 0 or salida is None:
                break
            entrada = salida

    if salida is None:
        print("El scraper no generó ningún archivo de salida.")
        sys.exit(1)
    print(f"\nSalida: {salida.relative_to(RAIZ)}")
    r = comparar(salida)
    imprimir_reporte(r)
    sys.exit(0 if r["correctos"] == r["total"] else 1)


if __name__ == "__main__":
    main()
