//Autor: Fábio Henrique Cabrini
//Resumo: Esse programa possibilita ligar e desligar o led onboard, além de mandar o status para o Broker MQTT possibilitando o Helix saber
//se o led está ligado ou desligado.
//Revisões:
//Rev1: 26-08-2023 Código portado para o ESP32 e para realizar a leitura de luminosidade e publicar o valor em um tópico aprorpiado do broker
//Autor Rev1: Lucas Demetrius Augusto
//Rev2: 28-08-2023 Ajustes para o funcionamento no FIWARE Descomplicado
//Autor Rev2: Fábio Henrique Cabrini
//Rev3: 1-11-2023 Refinamento do código e ajustes para o funcionamento no FIWARE Descomplicado
//Autor Rev3: Fábio Henrique Cabrini
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <time.h>

// Configurações - variáveis editáveis
const char* default_SSID = "Wokwi-GUEST"; // Nome da rede Wi-Fi
const char* default_PASSWORD = ""; // Senha da rede Wi-Fi
const char* default_BROKER_MQTT = "3.135.234.101"; // IP do Broker MQTT
const int default_BROKER_PORT = 1883; // Porta do Broker MQTT
const char* default_TOPICO_SUBSCRIBE = "/TEF/device096/cmd"; // Tópico MQTT de escuta
const char* default_TOPICO_PUBLISH_1 = "/TEF/device096/attrs"; // Tópico MQTT de envio de informações para Broker
const char* default_ID_MQTT = "fiware_096"; // ID MQTT
const int default_D4 = 2; // Pino do LED onboard
const unsigned long INTERVALO_LEITURA_MS = 2000; // 2000 = 2 s; 5000 = 5 s; minimo do DHT22: 2000 ms
// Declaração da variável para o prefixo do tópico
const char* topicPrefix = "device096";

// Variáveis para configurações editáveis
char* SSID = const_cast<char*>(default_SSID);
char* PASSWORD = const_cast<char*>(default_PASSWORD);
char* BROKER_MQTT = const_cast<char*>(default_BROKER_MQTT);
int BROKER_PORT = default_BROKER_PORT;
char* TOPICO_SUBSCRIBE = const_cast<char*>(default_TOPICO_SUBSCRIBE);
char* TOPICO_PUBLISH_1 = const_cast<char*>(default_TOPICO_PUBLISH_1);
char* ID_MQTT = const_cast<char*>(default_ID_MQTT);
int D4 = default_D4;

WiFiClient espClient;
PubSubClient MQTT(espClient);
char EstadoSaida = '0';

const int DHT_PIN = 15; // DATA do DHT22 na montagem do Wokwi
DHTesp dht;

void initSerial() {
    Serial.begin(115200);
}

void initWiFi() {
    delay(10);
    Serial.println("------Conexao WI-FI------");
    Serial.print("Conectando-se na rede: ");
    Serial.println(SSID);
    Serial.println("Aguarde");
    reconectWiFi();
}

void initMQTT() {
    MQTT.setServer(BROKER_MQTT, BROKER_PORT);
    MQTT.setCallback(mqtt_callback);
}

void setup() {
    InitOutput();
    initSerial();
    dht.setup(DHT_PIN, DHTesp::DHT22);
    initWiFi();
    configTime(0, 0, "pool.ntp.org", "time.nist.gov"); // Hora UTC por NTP
    initMQTT();
    delay(5000);
}

void loop() {
    VerificaConexoesWiFIEMQTT();
    EnviaEstadoOutputMQTT();
    handleLuminosity();
    MQTT.loop();
}

void reconectWiFi() {
    if (WiFi.status() == WL_CONNECTED)
        return;
    WiFi.begin(SSID, PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(100);
        Serial.print(".");
    }
    Serial.println();
    Serial.println("Conectado com sucesso na rede ");
    Serial.print(SSID);
    Serial.println("IP obtido: ");
    Serial.println(WiFi.localIP());

    // Garantir que o LED e seu estado fiquem desligados na reconexao
    digitalWrite(D4, LOW);
    EstadoSaida = '0';
}

void mqtt_callback(char* topic, byte* payload, unsigned int length) {
    String msg;
    for (int i = 0; i < length; i++) {
        char c = (char)payload[i];
        msg += c;
    }
    Serial.print("- Mensagem recebida: ");
    Serial.println(msg);

    // Forma o padrão de tópico para comparação
    String onTopic = String(topicPrefix) + "@on|";
    String offTopic = String(topicPrefix) + "@off|";

    // Compara com o tópico recebido
    if (msg.equals(onTopic)) {
        digitalWrite(D4, HIGH);
        EstadoSaida = '1';
    }

    if (msg.equals(offTopic)) {
        digitalWrite(D4, LOW);
        EstadoSaida = '0';
    }
}

void VerificaConexoesWiFIEMQTT() {
    reconectWiFi();
    if (!MQTT.connected())
        reconnectMQTT();
}

void EnviaEstadoOutputMQTT() {
    if (EstadoSaida == '1') {
        MQTT.publish(TOPICO_PUBLISH_1, "s|on");
        Serial.println("- Led Ligado");
    }

    if (EstadoSaida == '0') {
        MQTT.publish(TOPICO_PUBLISH_1, "s|off");
        Serial.println("- Led Desligado");
    }
    Serial.println("- Estado do LED onboard enviado ao broker!");
    delay(max(INTERVALO_LEITURA_MS, 2000UL)); // Garante o minimo do DHT22
}

void InitOutput() {
    pinMode(D4, OUTPUT);
    digitalWrite(D4, HIGH);
    boolean toggle = false;

    for (int i = 0; i <= 10; i++) {
        toggle = !toggle;
        digitalWrite(D4, toggle);
        delay(200);
    }
}

void reconnectMQTT() {
    while (!MQTT.connected()) {
        Serial.print("* Tentando se conectar ao Broker MQTT: ");
        Serial.println(BROKER_MQTT);
        if (MQTT.connect(ID_MQTT)) {
            Serial.println("Conectado com sucesso ao broker MQTT!");
            MQTT.subscribe(TOPICO_SUBSCRIBE);
        } else {
            Serial.println("Falha ao reconectar no broker.");
            Serial.println("Haverá nova tentativa de conexão em 2s");
            delay(2000);
        }
    }
}

void handleLuminosity() {
    // Leitura conjunta dos tres valores da mesma amostra.
    TempAndHumidity dados = dht.getTempAndHumidity();
    const int potPin = 34;
    int sensorValue = analogRead(potPin);
    // No modulo LDR do Wokwi, AO diminui quando a luz aumenta.
    int luminosity = map(sensorValue, 0, 4095, 100, 0); // 0=escuro, 100=claro; nao e lux

    if (isnan(dados.temperature) || isnan(dados.humidity)) {
        Serial.print("Falha no DHT22: ");
        Serial.println(dht.getStatusString());
        return;
    }

    Serial.printf("Temperatura: %.1f C  |  Umidade: %.1f %%  |  Luminosidade relativa: %d%%\n",
                  dados.temperature, dados.humidity, luminosity);

    time_t agora = time(nullptr);
    if (agora < 1704067200) { // Aguarda uma data valida, a partir de 2024.
        Serial.println("Aguardando sincronizacao da hora UTC; leitura ainda nao enviada.");
        return;
    }
    struct tm utc = {};
    gmtime_r(&agora, &utc);
    char timestamp[25];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
    time_t agoraBrasilia = agora - 3 * 3600; // UTC-3 somente para exibicao
    struct tm brasilia = {};
    gmtime_r(&agoraBrasilia, &brasilia);
    char dataHora[64];
    strftime(dataHora, sizeof(dataHora), "Data: %d/%m/%Y  |  Hora: %H:%M:%S (São Paulo)", &brasilia);
    Serial.println(dataHora);

    // Grupo Ultralight 2.0: horario UTC + luminosidade (p), temperatura (t), umidade (h).
    String mensagem = String(timestamp) + "|p|" + String(luminosity)
                    + "|t|" + String(dados.temperature, 1)
                    + "|h|" + String(dados.humidity, 1);
    if (MQTT.publish(TOPICO_PUBLISH_1, mensagem.c_str())) {
        Serial.println("Leituras enviadas ao broker.\n");
    } else {
        Serial.println("Falha ao enviar as leituras ao broker.");
    }
}
