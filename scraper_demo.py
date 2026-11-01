"""
Scraper de estados de envío.

Recorre un Excel con la columna ID_Envio, consulta cada número en el portal
de clientes de la empresa de transporte y escribe el estado en la columna
Estado_Scraping. Se puede cortar y reanudar: las filas que ya tienen estado
se saltan, así que basta con volver a lanzarlo sobre el archivo parcial.

En esta demo apunta al portal falso de portal_demo/.

Uso:
    python scraper_demo.py                          # abre un selector de archivo
    python scraper_demo.py --entrada lote.xlsx      # sin preguntas
    python scraper_demo.py --entrada lote.xlsx --headless
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Carga las credenciales (y cualquier ajuste de abajo) desde el .env
load_dotenv()


def _config(nombre, por_defecto):
    """Valor de configuración: variable de entorno / .env si existe, si no el de por defecto."""
    valor = os.environ.get(nombre, "")
    return type(por_defecto)(valor) if valor != "" else por_defecto


# ===========================================================================
# CONFIGURACIÓN. Cualquier valor se puede cambiar desde el .env con el mismo
# nombre. Al lado, el valor que se usaba contra el portal real.
# ===========================================================================
PORTAL_URL = _config("PORTAL_URL", "http://127.0.0.1:8765")
NOMBRE_COLUMNA_ID = _config("NOMBRE_COLUMNA_ID", "ID_Envio")
ARCHIVO_AVISOS = _config("ARCHIVO_AVISOS", "avisos.log")

INTENTOS_POR_PEDIDO = _config("INTENTOS_POR_PEDIDO", 3)                  # real: 3
FALLOS_SEGUIDOS_PARA_CORTAR = _config("FALLOS_SEGUIDOS_PARA_CORTAR", 5)  # real: no había corte
GUARDAR_CADA = _config("GUARDAR_CADA", 50)                               # real: 50

TIMEOUT_POR_DEFECTO_MS = _config("TIMEOUT_POR_DEFECTO_MS", 15000)  # real: 80000
ESPERA_RESULTADO_MS = _config("ESPERA_RESULTADO_MS", 10000)        # real: 10000
ESPERA_ESTADO_MS = _config("ESPERA_ESTADO_MS", 1500)               # real: 1500
ESPERA_TRAS_LOGIN_MS = _config("ESPERA_TRAS_LOGIN_MS", 1000)       # real: 8000
SLOW_MO_MS = _config("SLOW_MO_MS", 50)                             # real: 50

TEXTO_SIN_RESULTADOS = "No se encontraron envíos con esa referencia."

# Selectores del portal
SEL_USUARIO = "#usuario"
SEL_CONTRASENA = "#contrasena"
SEL_VERIFICACION = "#verificacion"
SEL_ACCEDER = "#acceder"
SEL_MENU_ENVIOS = "#menu-envios"
SEL_MENU_GESTION = "#menu-gestion > span"
SEL_MENU_CONSULTA = "#menu-consulta"
SEL_BUSCADOR = "#ref-envio"
SEL_BUSCAR = "#btn-buscar"
SEL_ESTADO = "#estado"
SEL_VOLVER = "#btn-volver"

URL_BUSQUEDA = f"{PORTAL_URL}/envios"


class CorteDelLote(Exception):
    """El lote se detiene a propósito: verificación manual o demasiados fallos seguidos."""


def registrar_aviso(mensaje):
    """
    Deja constancia de un aviso en ARCHIVO_AVISOS. Ocupa el lugar del correo
    de alerta del original: se llama en el mismo punto del flujo (y también
    cuando el lote se corta), sin credenciales de por medio.
    """
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {mensaje}"
    print(f"  [AVISO] {mensaje}")
    with open(ARCHIVO_AVISOS, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def seleccionar_archivo():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()  # Oculta la ventana base del sistema
    ruta = filedialog.askopenfilename(
        title="Selecciona el Excel de envíos",
        filetypes=[("Archivos Excel", "*.xlsx *.xls")]
    )
    return ruta


def nombre_base(archivo_entrada):
    """
    Ruta base del lote, para nombrar checkpoint y salidas junto al Excel de
    entrada. Si se reanuda desde una salida anterior (..._EN_CURSO,
    ..._PARCIAL_<fecha>, ..._FINAL_<fecha>) se quita el sufijo, para no
    terminar con nombres como lote_EN_CURSO_EN_CURSO.xlsx.
    """
    ruta = Path(archivo_entrada).resolve()
    base = re.sub(r"_(EN_CURSO|PARCIAL_[\d_-]+|FINAL_[\d_-]+)$", "", ruta.stem)
    return ruta.parent / base


def ruta_salida(base, sufijo):
    return base.parent / f"{base.name}_{sufijo}.xlsx"


def guardar_checkpoint(df, ruta_checkpoint):
    df.to_excel(ruta_checkpoint, index=False)
    print(f"  (Guardado de progreso: {ruta_checkpoint.name})")


def contar_pendientes(df):
    """Filas con ID que todavía no tienen estado."""
    ids = df[NOMBRE_COLUMNA_ID].astype(str).str.strip()
    con_id = df[NOMBRE_COLUMNA_ID].notna() & (ids != "") & (ids.str.lower() != "nan")
    estados = df["Estado_Scraping"]
    sin_estado = estados.isna() | (estados.astype(str).str.strip() == "")
    return int((con_id & sin_estado).sum())


def informar_pendientes(df, ruta):
    pendientes = contar_pendientes(df)
    if pendientes:
        print(f"Quedaron {pendientes} pedidos pendientes. "
              f"Vuelve a ejecutar el script eligiendo '{ruta.name}' para completarlos.")
    else:
        print("No quedó ningún pedido pendiente.")


def iniciar_sesion(page, usuario, contrasena):
    # Sin sesión, el portal redirige al login
    page.goto(URL_BUSQUEDA)
    print("Rellenando usuario y contraseña...")
    page.locator(SEL_USUARIO).fill(usuario)
    page.locator(SEL_CONTRASENA).fill(contrasena)

    print("Marcando la casilla de verificación...")
    try:
        page.locator(SEL_VERIFICACION).click(timeout=5000)
    except PlaywrightTimeoutError:
        print("No se encontró la casilla de verificación.")

    print("Comprobando si el botón 'Acceder' está habilitado...")
    boton_acceder = page.locator(SEL_ACCEDER)
    boton_habilitado = False
    for _ in range(10):
        if boton_acceder.is_enabled():
            boton_habilitado = True
            break
        time.sleep(0.5)

    if not boton_habilitado:
        # Antes: correo de alerta y 5 minutos de espera a que alguien
        # resolviera el CAPTCHA. Ahora el lote se corta y queda registrado.
        registrar_aviso("El portal pide verificación manual (CAPTCHA). No se procesó ningún pedido.")
        raise CorteDelLote("el portal pide verificación manual")

    with page.expect_navigation():
        boton_acceder.click()
    if page.locator(SEL_ACCEDER).count() > 0:
        raise CorteDelLote("el portal rechazó el usuario o la contraseña")
    print("¡Sesión iniciada correctamente!")


def ir_a_consulta(page):
    print("Navegando a la sección de consulta de envíos...")
    page.wait_for_timeout(ESPERA_TRAS_LOGIN_MS)
    page.locator(SEL_MENU_ENVIOS).first.click(force=True)
    # Medio segundo para que el menú se despliegue
    page.wait_for_timeout(500)
    page.locator(SEL_MENU_GESTION).click()
    with page.expect_navigation():
        page.locator(SEL_MENU_CONSULTA).click()


def leer_estado(page):
    """
    La casilla Estado se pinta vacía y el portal la rellena un momento
    después. Se sondea hasta ESPERA_ESTADO_MS: si sigue vacía, el envío
    existe pero no tiene estado asignado.
    """
    casilla = page.locator(SEL_ESTADO)
    limite = time.monotonic() + ESPERA_ESTADO_MS / 1000
    while True:
        valor = casilla.input_value().strip()
        if valor or time.monotonic() >= limite:
            return valor
        page.wait_for_timeout(100)


def volver_a_busqueda(page):
    try:
        with page.expect_navigation(timeout=ESPERA_RESULTADO_MS):
            page.locator(SEL_VOLVER).click()
    except Exception:
        # Si "Volver" no responde, se entra al buscador directamente
        try:
            page.goto(URL_BUSQUEDA)
        except Exception:
            pass


def consultar_estado(page, id_envio):
    """
    Busca un envío y devuelve:
      - el texto de la casilla Estado, p. ej. "ENTREGADO - 12/01/26 12:08"
      - "Sin registro" si el portal no lo encuentra
      - "Sin estado" si lo encuentra pero la casilla viene vacía
      - None si tras INTENTOS_POR_PEDIDO intentos no hubo una respuesta útil
    """
    for intento in range(1, INTENTOS_POR_PEDIDO + 1):
        try:
            if page.locator(SEL_BUSCADOR).count() == 0:
                # Página de error, navegación a medias o sesión caducada:
                # se vuelve al buscador antes de intentar
                page.goto(URL_BUSQUEDA)

            if page.locator(SEL_BUSCADOR).count() == 0:
                motivo = "no aparece el buscador (¿sesión caducada?)"
            else:
                page.locator(SEL_BUSCADOR).fill(id_envio)
                # El botón Buscar envía el formulario unos milisegundos
                # DESPUÉS del clic. Sin esperar a la página nueva, lo que se
                # leía a continuación podía ser todavía la página anterior:
                # tras un "sin resultados", el pedido siguiente quedaba
                # marcado "Sin registro" en falso.
                with page.expect_navigation(timeout=ESPERA_RESULTADO_MS):
                    page.locator(SEL_BUSCAR).click()

                if page.get_by_text(TEXTO_SIN_RESULTADOS).count() > 0:
                    # Se queda en el buscador: no hace falta "Volver"
                    return "Sin registro"

                if page.locator(SEL_ESTADO).count() > 0:
                    estado = leer_estado(page) or "Sin estado"
                    volver_a_busqueda(page)
                    return estado

                motivo = "el portal respondió sin estado ni aviso (¿error del servidor?)"

        except PlaywrightTimeoutError:
            motivo = "el portal no respondió a tiempo"

        print(f"  [!] {motivo}. Reintentando... ({intento}/{INTENTOS_POR_PEDIDO})")
        try:
            page.goto(URL_BUSQUEDA)
        except Exception:
            pass

    return None


def procesar_pedidos(page, df, ruta_checkpoint):
    """
    Recorre todas las filas sin Estado_Scraping y les saca el estado. Un
    pedido que no responde queda vacío (se reintentará en la próxima
    corrida), pero si fallan FALLOS_SEGUIDOS_PARA_CORTAR seguidos el problema
    ya no es de un pedido sino del portal o de la sesión, y el lote se corta.
    """
    print("Comenzando el escaneo de pedidos...")
    procesados_desde_ultimo_guardado = 0
    fallos_seguidos = 0

    for index, row in df.iterrows():

        # Saltamos si ya tiene estado: esto es lo que permite reanudar
        if pd.notna(row.get('Estado_Scraping')) and str(row.get('Estado_Scraping')).strip() != "":
            continue

        id_envio = str(row[NOMBRE_COLUMNA_ID]).strip()
        if id_envio.lower() == 'nan' or not id_envio:
            continue

        print(f"Procesando ID: {id_envio} ({index + 1}/{len(df)})")
        estado = consultar_estado(page, id_envio)

        if estado is None:
            fallos_seguidos += 1
            print(f"  [X] Sin respuesta para {id_envio}. Queda pendiente "
                  f"({fallos_seguidos}/{FALLOS_SEGUIDOS_PARA_CORTAR} fallos seguidos).")
            if fallos_seguidos >= FALLOS_SEGUIDOS_PARA_CORTAR:
                raise CorteDelLote(f"{fallos_seguidos} pedidos seguidos sin respuesta "
                                   "(portal caído o sesión caducada)")
        else:
            fallos_seguidos = 0
            df.at[index, 'Estado_Scraping'] = estado
            print(f" -> Estado capturado: {estado}")

        # Guardado periódico: si el proceso muere sin pasar por el guardado
        # de emergencia (se cierra la ventana, se va la luz), se pierden como
        # mucho GUARDAR_CADA pedidos.
        procesados_desde_ultimo_guardado += 1
        if procesados_desde_ultimo_guardado >= GUARDAR_CADA:
            guardar_checkpoint(df, ruta_checkpoint)
            procesados_desde_ultimo_guardado = 0

    return df


def run(playwright, archivo_excel, headless=False):
    """Devuelve 0 si el lote terminó, 2 si se cortó, 1 si no pudo empezar."""
    tiempo_inicio = time.time()
    usuario = os.environ.get("PORTAL_USUARIO")
    contrasena = os.environ.get("PORTAL_CONTRASENA")
    if not usuario or not contrasena:
        print("ERROR: Faltan PORTAL_USUARIO / PORTAL_CONTRASENA en el .env")
        return 1

    try:
        print("Cargando base de datos de envíos...")
        # El ID se lee como texto para evitar recortes o notación científica
        df = pd.read_excel(archivo_excel, dtype={NOMBRE_COLUMNA_ID: str})
        if NOMBRE_COLUMNA_ID not in df.columns:
            print(f"ERROR: El Excel no tiene la columna '{NOMBRE_COLUMNA_ID}'.")
            return 1
        if 'Estado_Scraping' not in df.columns:
            df['Estado_Scraping'] = ""
        df['Estado_Scraping'] = df['Estado_Scraping'].astype(object)
    except Exception as e:
        print(f"Error al leer el archivo Excel: {e}")
        return 1

    base = nombre_base(archivo_excel)
    ruta_checkpoint = ruta_salida(base, "EN_CURSO")

    browser = playwright.chromium.launch(headless=headless, slow_mo=SLOW_MO_MS)
    page = browser.new_context().new_page()
    page.set_default_timeout(TIMEOUT_POR_DEFECTO_MS)

    try:
        try:
            iniciar_sesion(page, usuario, contrasena)
            ir_a_consulta(page)
        except CorteDelLote as e:
            print(f"\n[ALERTA] No se pudo entrar al portal: {e}. El archivo de entrada queda igual.")
            return 2

        try:
            df = procesar_pedidos(page, df, ruta_checkpoint)
        except Exception as e:
            # --- GUARDADO DE EMERGENCIA ---
            if isinstance(e, CorteDelLote):
                registrar_aviso(f"Lote cortado: {e}")
            print(f"\n[ALERTA] Proceso interrumpido. Motivo: {e}")
            ruta_parcial = ruta_salida(base, f"PARCIAL_{datetime.now():%d-%m-%Y_%H-%M-%S}")
            df.to_excel(ruta_parcial, index=False)
            print(f"Archivo '{ruta_parcial.name}' guardado con los estados capturados hasta el momento.")
            informar_pendientes(df, ruta_parcial)
            return 2

        ruta_final = ruta_salida(base, f"FINAL_{datetime.now():%d-%m-%Y_%H-%M-%S}")
        df.to_excel(ruta_final, index=False)
        print(f"\n¡Éxito! Archivo guardado correctamente como: {ruta_final.name}")
        informar_pendientes(df, ruta_final)
        minutos, segundos = divmod(int(time.time() - tiempo_inicio), 60)
        print(f"Tiempo total de procesamiento: {minutos} minutos y {segundos} segundos.\n")
        return 0

    finally:
        print("Cerrando el navegador...")
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Consulta el estado de una lista de envíos en el portal.")
    parser.add_argument("--entrada", help="Excel con la columna ID_Envio. Si se omite, se abre un selector.")
    parser.add_argument("--headless", action="store_true", help="Navegador sin ventana.")
    args = parser.parse_args()

    interactivo = args.entrada is None
    codigo = 1
    try:
        if interactivo:
            print("Por favor, selecciona el archivo en la ventana emergente...")
        archivo_excel = args.entrada or seleccionar_archivo()
        if not archivo_excel:
            print("No se seleccionó ningún archivo. Finalizando programa.")
        else:
            with sync_playwright() as playwright:
                codigo = run(playwright, archivo_excel, headless=args.headless)
    except Exception as e:
        print(f"\nError fatal: {e}")
    finally:
        if interactivo:
            input("\nEl programa ha finalizado o se detuvo. Presiona ENTER para cerrar esta ventana...")
    sys.exit(codigo)


if __name__ == "__main__":
    main()
