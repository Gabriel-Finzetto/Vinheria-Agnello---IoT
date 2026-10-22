import ctypes
import os
import socket
import threading
import webbrowser
from ctypes import wintypes

import adaptador_smart_data_models as adaptador
import painel_historico_device096 as painel


URL_PAINEL = f"http://{painel.DASH_HOST}:{painel.DASH_PORT}"


def reservar_execucao():
    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, "Local\\VinheriaAgnelloIoT096")
    erro = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(erro)
    if erro == 183:
        kernel32.CloseHandle(handle)
        raise RuntimeError("O projeto ja esta aberto. Use a janela existente.")
    return kernel32, handle


def conferir_porta():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as servidor:
        try:
            servidor.bind((painel.DASH_HOST, painel.DASH_PORT))
        except OSError as erro:
            raise RuntimeError(
                f"A porta {painel.DASH_PORT} esta ocupada. Encerre a outra instancia do painel."
            ) from erro


def abrir_painel(encerrado):
    for _ in range(60):
        if encerrado.is_set():
            return
        try:
            with socket.create_connection((painel.DASH_HOST, painel.DASH_PORT), timeout=0.5):
                webbrowser.open(URL_PAINEL)
                return
        except OSError:
            encerrado.wait(0.5)
    print(f"Abra o painel manualmente: {URL_PAINEL}")


def main():
    reserva = None
    encerrado = threading.Event()
    try:
        reserva = reservar_execucao()
        conferir_porta()
        print(f"Painel: {URL_PAINEL}")
        print("Painel e adaptador funcionam nesta janela. Para encerrar, use Ctrl+C.")
        threading.Thread(target=adaptador.main, daemon=True).start()
        threading.Thread(target=abrir_painel, args=(encerrado,), daemon=True).start()
        painel.app.run(
            debug=False,
            use_reloader=False,
            host=painel.DASH_HOST,
            port=painel.DASH_PORT,
        )
    except (RuntimeError, OSError) as erro:
        print(f"Nao foi possivel iniciar: {erro}")
        return 1
    except KeyboardInterrupt:
        print("\nProjeto encerrado.")
    finally:
        encerrado.set()
        if reserva is not None:
            kernel32, handle = reserva
            kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
