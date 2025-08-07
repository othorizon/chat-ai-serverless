from paho.mqtt import client as mqtt_client

from config import MQTT_BROKER,MQTT_PORT,MQTT_USERNAME,MQTT_PASSWORD,MQTT_CA_CERTS_CONTENT, get_mqtt_ca_certs_filepath



class MQTTClient:
    def __init__(self, device_id:str,on_message_callback):
        self.broker = MQTT_BROKER
        self.port = MQTT_PORT
        self.username = MQTT_USERNAME
        self.password = MQTT_PASSWORD
        self.ca_certs = get_mqtt_ca_certs_filepath(MQTT_CA_CERTS_CONTENT)
        
        self.client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
        self.client.tls_set(ca_certs=self.ca_certs)
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self.on_connect
        self.client.on_message = on_message_callback
        self.is_running = False
        self.device_id = device_id

    def on_connect(self, client, userdata, connect_flags, reason_code, properties):
        print(f"Connected to MQTT Broker! reason_code: {reason_code}, connect_flags: {connect_flags}, properties: {properties}")
        # 连接后自动订阅
        self.client.subscribe(f"memory/device/{self.device_id}", qos=1)
    def run(self):
        self.is_running = True
        self.client.connect(self.broker, self.port)
        self.client.loop_start()
        print("MQTT client loop started.")

    def publish(self,topic:str,msg:str):
        result = self.client.publish(topic, msg, qos=1)
        status = result[0]
        if status == 0:
            print(f"Send `{msg}` to topic `{topic}`")
        else:
            print(f"Failed to send message to topic {topic}")
