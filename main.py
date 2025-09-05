import asyncio
from src.web import webserver
from threading import Thread


async def main():
    # roda o loop da loja em paralelo
    asyncio.create_task(webserver.loop_dados_site())

    # inicia o Flask em thread separada
    def run_flask():
        webserver.iniciar_webserver()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # mantém o loop vivo
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
