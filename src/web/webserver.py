from flask import Flask, send_from_directory, jsonify , redirect , render_template , request, session , url_for, abort
from flask_session import Session
from pymongo import MongoClient
from types import SimpleNamespace
import threading , os , discord.app_commands , re , time , secrets , requests,asyncio , logging , aiohttp , random,string , pytz
from src.services.connection.database import BancoUsuarios ,BancoServidores , BancoLoja , BancoBot, BancoLogs
from flask import request
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from collections import Counter
from datetime import datetime, timedelta, timezone







# ======================================================================
#Gerador de ID de 6 digitos
def gerar_id_unica(tamanho=6):
    caracteres = string.ascii_letters + string.digits  # Letras maiúsculas, minúsculas e números
    return ''.join(random.choice(caracteres) for _ in range(tamanho))









# ======================================================================
# LOADS DAS INFORMAÇÕES IMPORTANTES DO .ENV

load_dotenv(os.path.join(os.path.dirname(__file__), '.env')) #load .env da raiz
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")




# ======================================================================
# CONEXÂO COM MONGODB PARA SESSÔES

mongo_uri = os.getenv("MONGO_URI")
mongo_client = MongoClient(mongo_uri)
db_connection = mongo_client["brix"]
db_connection['sessions'].create_index('expiration', expireAfterSeconds=0)





# ======================================================================
#PARTE DO INICIO DA SESSÂO FLASK COM MONGODB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR) , template_folder=".")
app.config['SESSION_TYPE'] = 'mongodb'
app.config['SESSION_MONGODB'] = mongo_client
app.config['SESSION_MONGODB_DB'] = 'brix'
app.config['SESSION_MONGODB_COLLECT'] = 'sessions'
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['PERMANENT_SESSION_LIFETIME'] = 345600  # 4 dias em segundos





# Caminho absoluto da pasta de assets
ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets"))

# Serve arquivos estáticos direto em /assets/...
@app.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory(ASSETS_DIR, filename)




# ======================================================================
# CRIANDO A SESSÃO E OS CACHES NECESSARIOS
Session(app)
status_cache = {} # vai armazenar os dados do bot
loja_cache = {} # vai armazenar os itens da loja







# ======================================================================
#PARTE DA SOLICITAÇÂO DO DISCORD PARA DASHBOARD
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
REDIRECT_URI = os.getenv("REDIRECT_URI")
SCOPES = "identify guilds"
DISCORD_AUTH_URL = f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}"












# ======================================================================
#FUNÇÂO PARA PUXAR OS COMANDOS DO BOT
def extrair_comandos_grupo(grupo, prefixo=""):
    comandos = []
    for cmd in sorted(grupo.commands, key=lambda c: c.name):
        if isinstance(cmd, discord.app_commands.Group):
            comandos.extend(extrair_comandos_grupo(cmd, prefixo=f"{prefixo}{grupo.name} "))
        else:
            comandos.append({
                "nome": f"{prefixo}{grupo.name} {cmd.name}",
                "descricao": getattr(cmd, "description", "Sem descrição"),
                "opcoes": [
                    {
                        "nome": opt.name,
                        "tipo": str(opt.type),
                        "descricao": opt.description,
                        "obrigatorio": opt.required
                    }
                    for opt in getattr(cmd, "parameters", [])
                ]
            })
    return comandos









# ======================================================================
# FUNÇÃO PARA ATUALIZAR O CACHE DA LOJA
def atualizar_loja_cache():
    global loja_cache
    try:
        filtro = {"braixencoin": {"$exists": True}}
        pymongo_cursor = BancoLoja.select_many_document(filtro)
        
        # Força a leitura dentro do try
        dados = list(pymongo_cursor)[::-1]
        
        nova_cache = []
        for item in dados:
            nova_cache.append({
                "_id": item.get("_id", "Sem id"),
                "name": item.get("name", "Sem nome"),
                "descricao": item.get("descricao", "Sem descrição"),
                "url": item.get("url", ""),  # URL da imagem
                "braixencoin": f"{item.get('braixencoin', 0):,}".replace(",", "."),
                "graveto": f"{item.get('graveto', 0):,}".replace(",", "."),
                "raridade": item.get("raridade", 0),
                "font_color": item.get("font_color", 0)
            })
        loja_cache = nova_cache
        print(f"Update Loja Itens: {len(loja_cache)}")
    
    except Exception as e:
        # Apenas loga o erro, mantém a cache antiga
        print(f"[ERRO] Falha ao atualizar cache dos itens da loja, mantendo dados antigos: {e}")












# ======================================================================
#FUNÇÃO PARA ATUALIZAR O CACHE DOS COMANDOS
def atualizar_status_cache():
    global status_cache

    try:
        aplication = requests.get("https://discord.com/api/applications/@me", headers={"Authorization": f"Bot {DISCORD_TOKEN}"}).json()
        list_commands = requests.get(f"https://discord.com/api/applications/{aplication['id']}/commands", headers={"Authorization": f"Bot {DISCORD_TOKEN}"}).json()

        # pega os servidores parceiros no banco
        parceiros = BancoServidores.select_many_document({"partner": True})
        servidores = []
        for s in parceiros:
            try:
                guild = requests.get( f"https://discord.com/api/v10/guilds/{s['_id']}", headers={"Authorization": f"Bot {DISCORD_TOKEN}"} ).json()
                servidores.append({
                    "id": guild["id"],
                    "nome": guild["name"],
                    "icone": f"https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png"
                    if guild.get("icon") else None
                })
            except Exception as e:
                print(f"[ERRO] Falha ao buscar guild {s['_id']}: {e}")


        # Pega o documento único do bot
        dadosbot = BancoBot.insert_document()

        # Lista de comandos normais/slash já salva pelo BOT no banco
        comandos_slash = []

        def extrair_cmd(base_name, cmd):
            # ignora context menu
            if cmd.get("type") in (2, 3):
                return

            nome = f"{base_name} {cmd['name']}".strip()
            descricao = cmd.get("description", "Sem descrição")

            # verifica se tem subcomandos ou subgrupos
            options = cmd.get("options", [])
            if options and any(opt.get("type") in (1, 2) for opt in options):
                for opt in options:
                    if opt["type"] == 1:  # subcommand
                        extrair_cmd(nome, opt)
                    elif opt["type"] == 2:  # subcommand group
                        for sub in opt.get("options", []):
                            extrair_cmd(f"{nome} {opt['name']}", sub)
            else:
                comandos_slash.append({
                    "nome": nome,
                    "descricao": descricao,
                    "opcoes": [
                        {
                            "nome": opt.get("name"),
                            "tipo": str(opt.get("type")),
                            "descricao": opt.get("description", ""),
                            "obrigatorio": opt.get("required", False)
                        }
                        for opt in options if opt.get("type") not in (1, 2)
                    ]
                })

        # percorre todos os comandos globais
        for cmd in list_commands:
            extrair_cmd("", cmd)

        # organiza em ordem alfabética
        comandos_slash.sort(key=lambda x: x["nome"])

        status_cache = {
            "hora_atualização": time.strftime("%d/%m/%Y - %H:%M:%S", time.localtime()),
            "servidores": aplication['approximate_guild_count'],
            "lista_servidores": servidores,
            "usuarios": f"+{dadosbot.get('usuarios', 0)}",
            "braixencoin": f"+{dadosbot.get('braixencoin', 0)}",
            "nome": aplication['bot']['username'],
            "nome_completo": aplication['name'], 
            "num_comandos_slash": len(comandos_slash),
            "total_comandos": len(comandos_slash),
            "lista_comandos_slash": comandos_slash,
            "status_dashboard": dadosbot.get("status_dashboard", False),
            "profile_avatar" : dadosbot.get("profile_avatar", None),
            "profile_banner" : dadosbot.get("profile_banner", None)
        }

    except Exception as e:
        print(f"[ERRO] Falha ao atualizar cache: {e}")
        return
    














#--------------- COMANDO PARA BAIXAR ITENS DA LOJA PARA OS ARQUIVOS LOCAIS -----------
async def baixaritensloja(baixe_tudo: bool = False):
    filtro = {"_id": {"$ne": "diaria"}}
    itens = BancoLoja.select_many_document(filtro)
    IMAGE_SAVE_PATH = r"src/web/assets/backgrouds"

    if not os.path.exists(IMAGE_SAVE_PATH):
        os.makedirs(IMAGE_SAVE_PATH)

    print('🦊 - Iniciando Download dos itens da loja...')

    async with aiohttp.ClientSession() as session:
        async def baixar_imagem(item, idx):
            file_name = f"{item['_id']}.png"
            file_path = os.path.join(IMAGE_SAVE_PATH, file_name)

            # Checagem individual por arquivo
            if os.path.exists(file_path) and not baixe_tudo:
                if os.path.getsize(file_path) > 0:
                    return
                else:
                    print(f"⚠️ - Rebaixando {idx:02d} - {file_name} (arquivo vazio/corrompido)")

            #try:
            async with session.get(item['url']) as response:
                if response.status == 200:
                    content = await response.read()
                    if content:  # só salva se realmente veio algo
                        with open(file_path, 'wb') as f:
                            f.write(content)
                        print(f"🖼️ - Imagem Salva {idx:02d} - {file_name}")
                    else:
                        print(f"⚠️ - Resposta vazia para {item['url']}")
                else:
                    print(f'❌ - Falha ao baixar: {item["url"]} ({response.status})')
            #except Exception as e:
            #    print(f'❌ - Erro ao baixar {item["url"]}: {e}')

        tarefas = [baixar_imagem(item, idx + 1) for idx, item in enumerate(itens)]
        await asyncio.gather(*tarefas)

    print('✅ - Download concluído!')





# ======================================================================


# ================================== DIRECIONADORES DE ROTAS =========================

#CAMINHO DE ORIGEM DA PAGINA
@app.route('/')
def index():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    guilds = session.get("guilds", [])
    if not user or not guilds:
        return render_template("index.html")
    return render_template("index.html", user=user, guilds=guilds)









# ======================================================================
#CAMINHO PARA PAGINA DE COMANDOS
@app.route('/comandos')
def comandos():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    guilds = session.get("guilds", [])
    if not user or not guilds:
        return render_template("comandos.html")
    return render_template("comandos.html", user=user, guilds=guilds)










# ======================================================================
#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/loja')
def loja():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    guilds = session.get("guilds", [])
    if not user or not guilds:
        return render_template("loja.html")
    return render_template("loja.html", user=user, guilds=guilds )








# ======================================================================
#CAMINHO PARA PAGINA DE COMANDOS
@app.route('/stats')
def stats():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    guilds = session.get("guilds", [])
    if not user or not guilds:
        return render_template("estatisticas.html")
    return render_template("estatisticas.html", user=user, guilds=guilds)










#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/invite')
def invite():
    return redirect('https://discord.com/oauth2/authorize?client_id=983000989894336592&permissions=1514245844182&integration_type=0&scope=bot+applications.commands')












#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/vote')
def vote():
    return redirect('https://top.gg/bot/983000989894336592/vote')










#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/discord')
def servidordiscord():
    return redirect('https://discord.gg/ZRHwWydQFu')









#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/github')
def github():
    return redirect('https://github.com/O-Braixen/Brix')








#CAMINHO PARA PAGINA DA LOJA DE ITENS DO BRIX
@app.route('/tos')
def tos():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    guilds = session.get("guilds", [])
    if not user or not guilds:
        return render_template("tos.html")
    return render_template("tos.html", user=user, guilds=guilds )











# ======================================================================
#CAMINHO PARA PAGINA DE ASSINATURA PREMIUM (NÃO FEITO AINDA)
#@app.route('/premium')
#def premium():
   # user = session.get("user")
   # guilds = session.get("guilds", [])
   # if not user or not guilds:
   #     return render_template("premium.html")
   # return render_template("premium.html", user=user, guilds=guilds)









# ======================================================================
# ESSE CARA AQUI DIRECIONA PARA A ORIGEM EM CASO DE PROBLEMAS
@app.route('/<path:path>')
def serve_file(path):
    return send_from_directory(BASE_DIR, path)











# ======================================================================
#CALLBACK DO LOGIN RELACIONADO AO RETORNO DO DISCORD
@app.route("/login")
def login():
    user = session.get("user")
    if isinstance(user, dict) and user.get("message") == "401: Unauthorized":
        user = None
        session["user"] = None
    access_token = session.get("access_token")

    if not user or not access_token:
        return redirect(DISCORD_AUTH_URL)
    
    return redirect("/dashboard")












# ======================================================================
#CAMINHO PARA A PAGINA DE DASHBOARD DO BRIX QUE TAMBÉM FAZ TODA A VERIFICAÇÃO
@app.route("/dashboard")
def dashboard():
    if status_cache.get("status_dashboard", False) is not True:
        return render_template("manutencao.html")

    user = session.get("user")
    access_token = session.get("access_token")
    guilds = session.get("guilds")
    last_update = session.get("last_update", 0)

    if not user or not access_token:
        return render_template("dashlogin.html")

    # ---------------------------
    # DADOS DO BANCO SEM CACHE (sempre atualizados)
    # ---------------------------
    bot_guild_docs = BancoServidores.select_many_document({"bot_in_guild": True})
    bot_guild_ids = {str(doc["_id"]) for doc in bot_guild_docs}

    user_doc = BancoUsuarios.insert_document(int(user['id']))
    if user_doc.get("ban"):
        session.clear()
        return render_template("banned.html")

    # ---------------------------
    # DADOS DO DISCORD (cache de 20 minutos)
    # ---------------------------
    if time.time() - last_update > 1200:
        try:
            all_guilds = requests.get( "https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"} ).json()
            user = requests.get( "https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"} ).json()

            session["guilds"] = all_guilds
            session["user"] = user
            session["last_update"] = time.time()

        except:
            session.clear()
            return render_template("dashlogin.html")
    else:
        all_guilds = guilds

    # ---------------------------
    # FILTRO E MARCAÇÃO DAS GUILDS
    # ---------------------------
    filtered_guilds = []
    try:
        for guild in all_guilds:
            user_is_owner = guild.get("owner", False)
            permissions = int(guild.get("permissions", 0))
            user_is_admin = (permissions & 0x8) != 0
            user_can_manage_server = (permissions & 0x20) != 0

            if user_is_owner or user_is_admin or user_can_manage_server:
                guild['bot_in_guild'] = guild['id'] in bot_guild_ids
                filtered_guilds.append(guild)
    except:
        session.clear()
        return render_template("dashlogin.html")

    filtered_guilds.sort(key=lambda g: not g.get('bot_in_guild', False))
    return render_template("dashboard.html", user=user, guilds=filtered_guilds)





















# ======================================================================
#CALLBACK DO LOGIN RELACIONADO AO RETORNO DO DISCORD QUE PEGA TUDO E DIRECIONA PARA DASHBOARD
@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/")

    data = {"client_id": CLIENT_ID,"client_secret": CLIENT_SECRET,"grant_type": "authorization_code","code": code,"redirect_uri": REDIRECT_URI,"scope": SCOPES,    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers).json()
    session.permanent = True
    access_token = token["access_token"]
    session["access_token"] = access_token
    # COLETAÇÃO DOS DADOS DO USUARIO E DE SUAS GUILDAS.
    user = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"}).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"}).json()
    bot_guilds = requests.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bot {DISCORD_TOKEN}"}).json()
    bot_guild_ids = {g['id'] for g in bot_guilds}  # transforma em set pra busca rápida
    
    session["user"] = user
    session["guilds"] = guilds
    session["last_update"] = time.time() #SALVO O HORARIO DO REGISTRO DOS DADOS
    session["bot_guilds"] = bot_guild_ids
    return redirect("/dashboard")














# ======================================================================
#FERRAMENTA DE LOGOUT PARA FINALIZAR O LOGIN DO USUARIO
@app.route("/logout")
def logout():
    session.clear()  # REMOVE TODOS OS DADOS DA SESSÃO INDICADA
    return redirect("/") 














# ======================================================================
#PAGINA DO USUARIO DA DASHBOARD
@app.route("/user")
def user_dash():
    try:
        if status_cache.get("status_dashboard",False) is not True:
            return render_template("manutencao.html")
    
        user = session.get("user")
        if not user:
            return redirect("/dashboard")
        usuario = BancoUsuarios.insert_document(int(user["id"]))
        usuario.get('premium', False)
        perfil = {
            "premium": usuario['premium'].strftime('%d/%m/%Y') if usuario.get('premium', False) else False,
            "xpg": f"{usuario.get('xpg', 0):,}".replace(",", "."),
            "graveto": f"{usuario.get('graveto', 0):,}".replace(",", "."),
            "braixencoin": f"{usuario.get('braixencoin', 0):,}".replace(",", "."),
            "descricao": usuario.get("descricao", ""),
            "aniversario": usuario.get('nascimento', '00/00/0000'),
            "notificacoes": usuario["dm-notification"], 
            "backgroud": usuario.get("backgroud", ""),          
            "backgrouds": usuario.get("backgrouds", [])   
            
        }
        # USANDO A LOJA_CACHE GLOBAL DIRETAMENTE PARA EVITAR REQUESTS 
        backgrounds_usuario = [  item for item in loja_cache if item["_id"] in perfil["backgrouds"]]
        # GARANTO QUE O ITEM ATUAL DO USUARIO VIRÁ PRIMEIRO NA LISTA
        backgroud_atual = perfil["backgroud"]
        if backgroud_atual and backgroud_atual not in [item["_id"] for item in backgrounds_usuario]:
            item_atual = next((item for item in loja_cache if item["_id"] == backgroud_atual), None)
            if item_atual:
                backgrounds_usuario.append(item_atual)
        backgrounds_usuario.sort(key=lambda x: (x["_id"] != backgroud_atual, -x["raridade"]))
        return render_template("user.html", user=user, perfil=perfil, backgrounds=backgrounds_usuario)
    except: return redirect("/login")















# ======================================================================
#SALVA DADOS DA PAGINA DO USUARIO
@app.route("/dashboard/save-user", methods=["POST"])
def salvar_perfil_usuario():
    try:
        user = session.get("user")
        if not user:
            return redirect("/dashboard")

        descricao = request.form.get("descricao", "").strip()[:150]  # limita a 150
        arte_perfil = request.form.get("arte_perfil", "").strip()

        updates = {}
        if descricao:
            updates["descricao"] = descricao  # INCLUI DESCRIÇÃO NOVA NO UPDATE
        if arte_perfil:
            updates["backgroud"] = arte_perfil  # INCLUI ARTE DO PERFIL NO UPDATE

        updates["dm-notification"] = "ativar_notificacoes" in request.form
        if updates:
            BancoUsuarios.update_document(int(user["id"]), updates) # REALIZO O UPDATE NO BANCO DE DADOS
        #return redirect("/dashboard")
        return jsonify({"ok": True})
    except: return redirect("/login")
















# ======================================================================
# PÁGINA DE CONTROLE DO SERVIDOR
@app.route("/server/<guild_id>")
def guild_dashboard(guild_id):
    try:
        if status_cache.get("status_dashboard",False) is not True:
            return render_template("manutencao.html")
    
        user = session.get("user")
        guild = next((g for g in session.get("guilds", []) if g["id"] == str(guild_id)), None)
        if not user or not guild:
            return redirect("/dashboard")
        
        user_is_owner = guild.get("owner", False)
        permissions = int(guild.get("permissions", 0))
        user_is_admin = (permissions & 0x8) != 0
        user_can_manage_server = (permissions & 0x20) != 0

        if user_is_owner or user_is_admin or user_can_manage_server:
            # --- PEGAR INFO PADRÃO DO BOT
            bot_info = requests.get( "https://discord.com/api/users/@me", headers={"Authorization": f"Bot {DISCORD_TOKEN}"} ).json()
            bot_default = {
                "nome": bot_info.get("global_name") or bot_info.get("username"),
                "bio": bot_info.get("bio") or "Biografia padrão do sistema.",
                "avatar": f"https://cdn.discordapp.com/avatars/{bot_info['id']}/{bot_info['avatar']}.png?size=2048" if bot_info.get("avatar") else None,
                "banner": f"https://cdn.discordapp.com/banners/{bot_info['id']}/{bot_info['banner']}.png?size=2048" if bot_info.get("banner") else None}
            
            guild = requests.get(f"https://discord.com/api/guilds/{guild_id}", headers={"Authorization": f"Bot {DISCORD_TOKEN}"}).json()
            # transforma roles em objetos com atributos
            roles = guild.get("roles", [])
            for r in roles:
                r["id"] = int(r["id"])  # garante int
            guild["roles"] = roles
            if guild.get("code",False):
                return redirect("/dashboard")
            # DADOS PROVENIENTES DO BANCO DE DADOS
            canais = requests.get(f"https://discord.com/api/guilds/{guild_id}/channels", headers={"Authorization": f"Bot {DISCORD_TOKEN}"}).json()
            text_channels = [ SimpleNamespace(**{ "id": int(c["id"]), "name": c["name"], "type": c["type"] }) for c in canais if c["type"] in (0, 5)]
            retbanco = BancoServidores.insert_document(int(guild_id))
            userpremium = BancoUsuarios.insert_document(int(user["id"]))
            return render_template("server.html", user=user, retbanco=retbanco, guild=guild , text_channels=text_channels , premium = userpremium.get('premium', False), bot_default=bot_default)
        
        return redirect("/dashboard")
    except: return redirect("/login")

















# ======================================================================
#SALVA DADOS DA PAGINA DE CONTROLE DO SERVIDOR
@app.route("/dashboard/save-guild", methods=["POST"])
def salvar_configuracoes():
    user = session.get("user")
    if not user:
        return redirect("/dashboard")
    guild_id = request.form.get("guild_id")
    if not guild_id:
        return "ID do servidor ausente", 400

    # Verifica se o user tem acesso ao servidor
    guilds = session.get("guilds", [])
    if not any(str(g["id"]) == guild_id for g in guilds):
        return "Acesso negado", 403

    # ✅ CARREGAR O BANCO SEMPRE (GET e POST)
    retbanco = BancoServidores.insert_document(int(guild_id))

    updates = {}
    unset_fields = {}

    # ---------------- ANIVERSÁRIO ----------------
    if "ativar_aniversario" in request.form:
        destaque = request.form.get("cargo_temp_aniversario")
        aniversario = {
            "canal": int(request.form.get("canal_aniversario")),
            "cargo": int(request.form.get("cargo_ping_aniversario")),
        }
        if destaque and destaque.isdigit() and int(destaque) > 0:
            aniversario["destaque"] = int(destaque)

        updates["aniversario"] = aniversario
    else:
        unset_fields["aniversario"] = 1

    # ---------------- BOAS-VINDAS ----------------
    if "ativar_boasvindas" in request.form:
        updates["boasvindas"] = {
            "canal": int(request.form.get("canal_boasvindas")),
            "mensagem": request.form.get("mensagem_boasvindas", "").replace('\r\n', '\n').replace('\r', '\n').strip(),
            "deletar": int(request.form.get("boasvindas_deletar", 0))
        }
    else:
        unset_fields["boasvindas"] = 1

    # ---------------- AUTOPHOX ----------------
    if "ativar_autophox" in request.form:
        updates["autophox"] = int(request.form.get("canal_autophox"))
    else:
        unset_fields["autophox"] = 1




    # ---------------- LOJA DE CORES ----------------
    if "ativar_loja_cores" in request.form:
        html = request.form.get("lista-itens-loja-html", "")
        soup = BeautifulSoup(html, "html.parser")
        itensloja = {}
            
        for item in soup.find_all(attrs={"data-id": True}):
            cargo_id = item["data-id"]
            texto = item.get_text()
            match = re.findall(r"\b\d+\b", texto)
            valor = int(match[-1]) if match else 0
            itensloja[str(cargo_id)] = str(valor)

        updates["itensloja"] = itensloja
        link_arte = request.form.get("link_arte_loja", "").strip()
        if link_arte:
            updates["lojabanner"] = link_arte
    else:
        unset_fields["itensloja"] = 1
        unset_fields["lojabanner"] = 1

    # ---------------- BUMP ----------------
    if "ativar_bump" in request.form:
        updates["bump-message"] = request.form.get("mensagem_bump", "").replace('\r\n', '\n').replace('\r', '\n').strip()
    else:
        unset_fields["bump-message"] = 1

    # ---------------- POKÉDAY ----------------
    if "ativar_pokeday" in request.form:
        ping_val = request.form.get("cargo_pokeday", "").strip()
        updates["pokeday"] = {
            "canal": int(request.form.get("canal_pokeday")),
            "ping": int(ping_val) if ping_val.isdigit() else None
        }
    else:
        unset_fields["pokeday"] = 1

    
    # ---------------- TROCAS POKÉMON ----------------
    if "ativar_trocas" in request.form:
        ping_val = request.form.get("cargo_trocas", "").strip()
        updates["trocas_aviso"] = {
            "canal": int(request.form.get("canal_trocas")),
            "cargo": int(ping_val) if ping_val.isdigit() else None
        }
    else:
        unset_fields["trocas_aviso"] = 1


    
    # ---------------- SEGURANÇA ----------------
    if "ativar_seguranca" in request.form:
        tempo_valor = request.form.get("tempo_antialt")
        unidade = request.form.get("unidade_antialt")  # minutos, horas ou dias
        acao = request.form.get("acao_antialt", "kick")
        notificar = "notificar_antialt" in request.form

        # Converte tempo pra segundos
        try:
            tempo_num = int(tempo_valor)
            if unidade == "minutos":
                tempo_segundos = tempo_num * 60
            elif unidade == "horas":
                tempo_segundos = tempo_num * 3600
            elif unidade == "dias":
                tempo_segundos = tempo_num * 86400
            else:
                tempo_segundos = tempo_num  # fallback, caso venha vazio
        except (TypeError, ValueError):
            tempo_segundos = 432000 # valor padrão para 5 dias em caso de erro
        
        if tempo_segundos > 2592000:
            tempo_segundos = 2592000
        antialt = {
            "seguranca.antialt.tempo": tempo_segundos,
            "seguranca.antialt.acao": acao,
            "seguranca.antialt.notificacao": notificar
        }
        updates.update(antialt)
    else:
        unset_fields["seguranca"] = 1
    








    # ---------------- SERVIDOR TAG ----------------
    if "ativar_servidor_tag" in request.form:
        cargo_val = request.form.get("cargo_servidor_tag", "").strip()
        notificar = "notificar_servidor_tag" in request.form  # True se checkbox marcado

        updates["tag_server"] = {
            "cargo": int(cargo_val) if cargo_val.isdigit() else None,
            "aviso_dm": notificar
        }
    else:
        unset_fields["tag_server"] = 1








# ---------------- LOJA VIP ----------------
    
    if "ativar_loja_vip" in request.form:
        html = request.form.get("lista-itens-loja-vip-html", "") or ""
        soup = BeautifulSoup(html, "html.parser")
        novos_itens = {}

        for item in soup.find_all(attrs={"data-id": True}):
            system_id = item["data-id"]
            try:
                cargo_id = int(item.get("data-cargo", 0))
            except (TypeError, ValueError):
                continue  # pular item inválido

            try:
                valor = int(item.get("data-valor", 0))
            except (TypeError, ValueError):
                valor = 0

            tempo_raw = item.get("data-tempo", "perm")
            if tempo_raw != "perm":
                try:
                    tempo = int(tempo_raw)
                except (TypeError, ValueError):
                    tempo = "perm"
            else:
                tempo = "perm"

            registrado = item.get("data-registro")
            try:
                registrado_int = int(registrado) if registrado is not None else int(user["id"])
            except Exception:
                registrado_int = int(user["id"])

            # Se system_id começa com "new-" => é item novo no client
            if isinstance(system_id, str) and system_id.startswith("new-"):
                novos_itens[gerar_id_unica()] = {
                    "cargo": cargo_id,
                    "valor": valor,
                    "tempo": tempo,
                    "registrado": registrado_int,
                }
            else:
                # item existente: preserva a chave como veio do client (presumivelmente é o system id antigo)
                # se a chave for numérica ou string, usa como está
                chave = system_id
                novos_itens[chave] = {
                    "cargo": cargo_id,
                    "valor": valor,
                    "tempo": tempo,
                    "registrado": registrado_int,
                }

        updates["lojavip"] = novos_itens

        link_arte_vip = (request.form.get("link_arte_loja_vip") or "").strip()
        if link_arte_vip:
            updates["lojavipbanner"] = link_arte_vip
    else:
        unset_fields["lojavip"] = 1
        unset_fields["lojavipbanner"] = 1









    # ---------------- CUSTOMIZAÇÃO DO BRIX ----------------
    if "ativar_customizar" in request.form:

        nome = request.form.get("brix_nome", "").strip()
        bio = request.form.get("brix_bio", "").strip()
        avatar = request.form.get("brix_avatar", "").strip()
        banner = request.form.get("brix_banner", "").strip()

        def normalize(v):
            return None if v in (None, "", "None") else v

        nome = normalize(nome)
        bio = normalize(bio)
        avatar = normalize(avatar)
        banner = normalize(banner)

        # ✅ Se tudo for None → ignora, não salva nada
        if not any([nome, bio, avatar, banner]):
            updates["custom"] = {"delete": True}
        else:
            updates["custom"] = {
                "ativo": False,
                "nome": nome,
                "bio": bio,
                "avatar": avatar,
                "banner": banner,
                "delete": False
            }

    else:
        # ✅ Só marca delete se tiver algo salvo antes
        if retbanco and retbanco.get("custom"):
            updates["custom"] = {"delete": True}






    # Aplica updates de uma vez só
    if updates:
        BancoServidores.update_document(int(guild_id), updates)
    if unset_fields:
        BancoServidores.delete_field(int(guild_id), unset_fields)

    #return redirect("/dashboard")
    return jsonify({"ok": True})





















# ======================================================================
# =============================== RESPOSTAS DE API =====================
# RETORNO DE STATUS DO BOT, COM COMANDOS E OUTROS DETALHES
@app.route('/api/status')
def status():
    if status_cache:
        return jsonify(status_cache)
    else:
        return jsonify({"status": "bot ainda iniciando..."})








# ======================================================================
# RETORNO PARA EXIBIR TODOS OS ITENS DA LOJA DO BOT NO SITE
@app.route('/api/loja')
def statusloja():
    if loja_cache:
        return jsonify(loja_cache)
    else:
        return jsonify({"status": "bot ainda iniciando..."})








# ======================================================================
# RETORNO PARA EXIBIR AS MÉTRICAS E ESTATÍSTICAS DO BOT NO SITE
@app.route("/api/metricas")
def api_metricas():
    tipo = int(request.args.get("tipo", 1))  # 1=entrada, 6=saída, 2=comandos, 3=usuários, 4=lat/ram, 5=premium
    dias = int(request.args.get("dias", 7))

    now = datetime.now(timezone.utc)
    inicio = now - timedelta(days=dias - 1)
    dias_lista = [(inicio + timedelta(days=i)).strftime("%d/%m") for i in range(dias)]

    # --- Consulta bruta
    eventos = BancoLogs.listar(limite=3000)
    eventos = [
        e for e in eventos
        if inicio <= e["timestamp"].replace(tzinfo=timezone.utc) <= now
    ]

    dados_grafico = []
    top_comando = None

    # ================================
    # ENTRADAS EM SERVIDORES (tipo 1, acao=entrada)
    # ================================
    if tipo == 1:
        eventos = [e for e in eventos if e["tipo"] == 1]
        agrupado = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            acao = e["dados"].get("acao")
            if dia not in agrupado:
                agrupado[dia] = {"entrada": 0, "saida": 0}

            if acao == "entrada":
                agrupado[dia]["entrada"] += 1
            elif acao == "saida":
                agrupado[dia]["saida"] += 1

        dados_grafico = [
            {
                "data": d,
                "entradas": agrupado.get(d, {}).get("entrada", 0),
                "saidas": agrupado.get(d, {}).get("saida", 0)
            }
            for d in dias_lista
        ]

    
    # ================================
    # SERVIDORES TOTAIS (tipo 6)
    # ================================
    elif tipo == 6:
        eventos = [e for e in eventos if e["tipo"] == 3]
        por_dia = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            if dia not in por_dia or e["timestamp"] > por_dia[dia]["timestamp"]:
                por_dia[dia] = e
        for d in dias_lista:
            e = por_dia.get(d)
            valor = e["dados"].get("total_servidores", 0) if e else 0
            dados_grafico.append({"data": d, "valor": valor})

    # ================================
    # COMANDOS USADOS (tipo 2)
    # ================================
    elif tipo == 2:
        eventos = [e for e in eventos if e["tipo"] == 2]
        comandos = [e["dados"].get("comando") for e in eventos if "dados" in e]
        if comandos:
            top_comando, _ = Counter(comandos).most_common(1)[0]

        agrupado = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            agrupado[dia] = agrupado.get(dia, 0) + 1
        dados_grafico = [{"data": d, "valor": agrupado.get(d, 0)} for d in dias_lista]

    # ================================
    # USUÁRIOS (tipo 3)
    # ================================
    elif tipo == 3:
        eventos = [e for e in eventos if e["tipo"] == 3]
        por_dia = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            if dia not in por_dia or e["timestamp"] > por_dia[dia]["timestamp"]:
                por_dia[dia] = e
        for d in dias_lista:
            e = por_dia.get(d)
            valor = e["dados"].get("total_usuarios", 0) if e else 0
            dados_grafico.append({"data": d, "valor": valor})

    # ================================
    # LATÊNCIA E RAM (tipo 4)
    # ================================
    elif tipo == 4:
        eventos = [e for e in eventos if e["tipo"] == 4]
        agrupado = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            if dia not in agrupado:
                agrupado[dia] = {"lat": [], "ram": []}
            agrupado[dia]["lat"].append(e["dados"].get("latencia_ms", 0))
            agrupado[dia]["ram"].append(e["dados"].get("uso_ram_mb", 0))

        for d in dias_lista:
            if d in agrupado:
                lat_media = sum(agrupado[d]["lat"]) / len(agrupado[d]["lat"])
                ram_media = sum(agrupado[d]["ram"]) / len(agrupado[d]["ram"])
            else:
                lat_media = 0
                ram_media = 0
            dados_grafico.append({
                "data": d,
                "latencia": round(lat_media, 2),
                "ram": round(ram_media, 2)
            })

    # ================================
    # PREMIUM (tipo 5)
    # ================================
    elif tipo == 5:
        eventos = [e for e in eventos if e["tipo"] == 5]
        agrupado = {}
        for e in eventos:
            dia = e["timestamp"].astimezone(timezone.utc).strftime("%d/%m")
            agrupado[dia] = agrupado.get(dia, 0) + 1
        dados_grafico = [{"data": d, "valor": agrupado.get(d, 0)} for d in dias_lista]

    return jsonify({"dados": dados_grafico, "top_comando": top_comando})

















# PARTE DO SISTEMA CDN DO SITE PARA AS MÍDIAS
# Caminhos absolutos
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'assets'))
IMG_FOLDER = os.path.join(BASE_DIR, 'img')
BG_FOLDER = os.path.join(BASE_DIR, 'backgrouds')
CDN_FOLDER = os.path.join(BASE_DIR, 'cdn')

@app.route("/cdn")
def cdn_page():
    files = []

    def listar_arquivos(pasta_base):
        lista = []
        for root, _, filenames in os.walk(pasta_base):
            for f in filenames:
                rel_path = os.path.relpath(os.path.join(root, f), pasta_base)
                lista.append(rel_path.replace("\\", "/"))
        return lista

    # Junta tudo num só
    files.extend(listar_arquivos(IMG_FOLDER))
    files.extend(listar_arquivos(BG_FOLDER))
    files.extend(listar_arquivos(CDN_FOLDER))

    return render_template("cdn.html", files=files)

@app.route("/cdn/<path:filename>")
def serve_cdn_file(filename):
    # Procura o arquivo em todas as pastas
    for folder in [IMG_FOLDER, BG_FOLDER, CDN_FOLDER]:
        full_path = os.path.join(folder, filename)
        if os.path.exists(full_path):
            return send_from_directory(folder, filename)
    return "Arquivo não encontrado", 404








# ======================================================================
# RETORNO DE UMA VENDA PELO SISTEMA MERCADO PAGO
@app.route('/comprapremium', methods=['POST'])
def webhook_mercadopago():
    data = request.json
    print("Webhook recebido:", data)
    
    return "OK", 200













# ======================================================================
# INICIA O WEBSERVER PARA RODAR TODA A PARTE DO SITE

def iniciar_webserver():
    threading.Thread(target=_run_web).start()
    




# ======================================================================
# REALIZA O LOOP PARA ATUALIZAR OS DADOS DE CACHE
async def loop_dados_site():
    while True:
        await asyncio.sleep(2)
        atualizar_status_cache()
        atualizar_loja_cache()
        await baixaritensloja()
        await asyncio.sleep(14400) #4 horas










# ======================================================================
#RODA O WEBSERVER DE VEZ OCULTANDO OS LOGS EXAGERADOS
def _run_web():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    #app.logger.disabled = True
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
