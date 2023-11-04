import socket
import re
from flask import Flask, request, jsonify
from twilio.rest import Client
import time
import threading
import setproctitle

# Set the custom process name
setproctitle.setproctitle("sms")

app = Flask(__name__)

# Set this variable to enable/disable private mode
private_mode = False  # Change this value as needed

# List of callsigns allowed to send messages if private_mode is TRUE. Accepts ALL SSIDs for a CALLSIGN listed.
allowed_callsigns = ['CALLSIGN0', 'CALLSIGN1', 'CALLSIGN2']  # Add more callsigns as needed

# Twilio credentials
TWILIO_ACCOUNT_SID = 'SID'
TWILIO_AUTH_TOKEN = 'AUTH'
TWILIO_PHONE_NUMBER = '+NUMBER'  # Your Twilio phone number

# APRS credentials
APRS_CALLSIGN = 'CALL'
APRS_PASSCODE = 'PASS'
APRS_SERVER = 'rotate.aprs2.net'
APRS_PORT = 14580

# Initialize the socket
aprs_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Declare socket_ready as a global variable
socket_ready = False

# Dictionary to store the last number an APRS user messaged (callsign: last_number)
last_message_number = {}

# Dictionary to store the last received APRS message ID for each user
user_last_message_id = {}

processed_message_ids = set()

# Outside the main loop, initialize a dictionary to store message history
received_aprs_messages = {}

received_acks = {}

RETRY_INTERVAL = 90  # Adjust this as needed

MAX_RETRIES = 4  # Adjust this as needed

def send_ack_message(sender, message_id):
    ack_message = 'ack{}'.format(message_id)
    sender_length = len(sender)
    spaces_after_sender = ' ' * max(0, 9 - sender_length)
    ack_packet_format = '{}>APOSMS::{}{}:{}\r\n'.format(APRS_CALLSIGN, sender, spaces_after_sender, ack_message)
    ack_packet = ack_packet_format.encode()
    aprs_socket.sendall(ack_packet)
    print("Sent ACK to {}: {}".format(sender, ack_message))
    print("Outgoing ACK packet: {}".format(ack_packet.decode()))

def send_rej_message(sender, message_id):
    rej_message = 'rej{}'.format(message_id)
    sender_length = len(sender)
    spaces_after_sender = ' ' * max(0, 9 - sender_length)
    rej_packet_format = '{}>APOSMS::{}{}:{}\r\n'.format(APRS_CALLSIGN, sender, spaces_after_sender, rej_message)
    rej_packet = rej_packet_format.encode()
    aprs_socket.sendall(rej_packet)
    print("Sent REJ to {}: {}".format(sender, rej_message))
    print("Outgoing REJ packet: {}".format(rej_packet.decode()))


def send_sms(twilio_phone_number, to_phone_number, from_callsign, body_message):
    # Initialize the Twilio client
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        # Send SMS using the Twilio API
        message = client.messages.create(
            body="@{} {}".format(from_callsign, body_message),
            from_=twilio_phone_number,
            to=to_phone_number
        )
        print("SMS sent successfully.")
        print("SMS SID:", message.sid)
    except Exception as e:
        print("Error sending SMS:", str(e))


def format_aprs_packet(callsign, message):
    sender_length = len(callsign)
    spaces_after_sender = ' ' * max(0, 9 - sender_length) #1,9 - Changed 9-16
    aprs_packet_format = '{}>APOSMS::{}{}:{}\r\n'.format(APRS_CALLSIGN, callsign, spaces_after_sender, message)
    return aprs_packet_format

# Dictionary to store the mapping of aliases (callsigns) to phone numbers
alias_map = {
    'alias1': '9876543210',  # Replace 'alias1' with the desired alias and '1234567890' with the corresponding phone number.
    'alias2': '9876543210',  # Add more entries as needed for other aliases and phone numbers.
    # Add more entries as needed.        
}

def find_phone_number_from_alias(alias):
    return alias_map.get(alias.lower())

# Create a new dictionary to store the reverse mapping of phone numbers to aliases
reverse_alias_map = {v: k for k, v in alias_map.items()}


@app.route('/sms', methods=['POST'])
def receive_sms():
    # Parse the incoming SMS message
    data = request.form
    from_phone_number = data['From']
    body_message = data['Body']
    print (body_message)
    
    if body_message.startswith('@'):
        parts = body_message.split(' ', 1)
        if len(parts) == 2:
            # Extract the 10-digit phone number from the sender's phone number
            sender_phone_number = from_phone_number[-10:]
            callsign = parts[0][1:].upper()
            aprs_message = parts[1]

            # Get the last APRS message ID sent to this user
            last_message_id = user_last_message_id.get(from_phone_number, 0)
            last_message_id += 1
            user_last_message_id[from_phone_number] = last_message_id

            # Use the reverse alias mapping to check if the sender's phone number has an associated alias
            alias = reverse_alias_map.get(sender_phone_number.lower())
            if alias:
                sender_phone_number = alias

            # Format the APRS packet and send it to the APRS server
            aprs_packet = format_aprs_packet(callsign, "@{} {}".format(sender_phone_number, aprs_message + "{" + str(last_message_id)))
            aprs_socket.sendall(aprs_packet.encode())

            time.sleep(5)  # Sleeping here allows time for incoming ack before retry

            retry_count = 0
            ack_received = False

            while retry_count < MAX_RETRIES and not ack_received:
                if str(last_message_id) in received_acks.get(callsign, set()):
                    print("Message ACK received. No further retries needed.")
                    ack_received = True
                    retry_count = 0
                    received_acks.get(callsign, set()).discard(str(last_message_id))
                else:
                    print("ACK not received. Retrying in {} seconds.".format(RETRY_INTERVAL))
                    aprs_socket.sendall(aprs_packet.encode())
                    retry_count += 1
                    time.sleep(RETRY_INTERVAL)

            if ack_received:
                print("ACK received during retries. No further retries needed.")
            elif retry_count >= MAX_RETRIES:
                print("Max retries reached. No ACK received for the message.")

            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'Invalid SMS format'})
    else:
    
        print ("no callsign found")
        # Message without @callsign prefix
        callsign = last_message_number.get(from_phone_number[-10:], None)
        sender_phone_number = from_phone_number[-10:]
        print("From:", from_phone_number[-10:])
        print("Dictionary:", last_message_number)
        print("Callsign Found:", callsign)

        if callsign:
            # Extract the APRS message content
            aprs_message = body_message

            # Get the last APRS message ID sent to this user
            last_message_id = user_last_message_id.get(from_phone_number, 0)
            last_message_id += 1
            user_last_message_id[from_phone_number] = last_message_id
            
            # Use the reverse alias mapping to check if the sender's phone number has an associated alias
            alias = reverse_alias_map.get(sender_phone_number.lower())
            if alias:
                sender_phone_number = alias

            # Format the APRS packet and send it to the APRS server
            aprs_packet = format_aprs_packet(callsign, "@{} {}".format(sender_phone_number, aprs_message + "{" + str(last_message_id)))
            aprs_socket.sendall(aprs_packet.encode())

            print("Sent APRS message to {}: {}".format(callsign, aprs_message))
            print("Outgoing APRS packet: {}".format(aprs_packet.strip()))

            time.sleep(5)  # Sleeping here allows time for incoming ack before retry

            retry_count = 0
            ack_received = False

            while retry_count < MAX_RETRIES and not ack_received:
                if str(last_message_id) in received_acks.get(callsign, set()):
                    print("Message ACK received. No further retries needed.")
                    ack_received = True
                    retry_count = 0
                    received_acks.get(callsign, set()).discard(str(last_message_id))
                else:
                    print("ACK not received. Retrying in {} seconds.".format(RETRY_INTERVAL))
                    aprs_socket.sendall(aprs_packet.encode())
                    retry_count += 1
                    time.sleep(RETRY_INTERVAL)

            if ack_received:
                print("ACK received during retries. No further retries needed.")
            elif retry_count >= MAX_RETRIES:
                print("Max retries reached. No ACK received for the message.")

            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'No associated callsign found for the sender\'s phone number'})

def establish_aprs_connection():
    global aprs_socket, socket_ready

    while True:
        try:
            # Initialize the socket and connect to the APRS server
            aprs_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            aprs_socket.connect((APRS_SERVER, APRS_PORT))
            print("Connected to APRS server with callsign: {}".format(APRS_CALLSIGN))

            # Send login information with APRS callsign and passcode
            login_str = 'user {} pass {} vers SMS-Gateway 1.0 Beta\r\n'.format(APRS_CALLSIGN, APRS_PASSCODE)
            aprs_socket.sendall(login_str.encode())
            print("Sent login information.")

            # Set the socket_ready flag to indicate that the socket is ready for keepalives
            socket_ready = True

            # If the connection was successful, break out of the loop
            break

        except socket.error as e:
            print("Socket error:", str(e))
            socket_ready = False
            time.sleep(1)  # Wait for a while before attempting to reconnect

        except Exception as e:
            print("Error connecting to APRS server: {}".format(e))
            socket_ready = False
            time.sleep(1)  # Wait for a while before attempting to reconnect

def receive_aprs_messages():
    global socket_ready, last_message_number   # Declare that you're using the global variable

    while True:
        try:
            if not socket_ready:
                # Attempt to establish a new connection to the APRS server
                establish_aprs_connection()
                
            buffer = ""
            try:
                while True:
                    data = aprs_socket.recv(1024)
                    if not data:
                        break
                    
                    # Add received data to the buffer
                    buffer += data.decode()

                    # Split buffer into lines
                    lines = buffer.split('\n')

                    # Process each line
                    for line in lines[:-1]:
                        if line.startswith('#'):
                            continue

                        # Process APRS message
                        print("Received raw APRS packet: {}".format(line.strip()))
                        parts = line.strip().split(':')
                        if len(parts) >= 2:
                            from_callsign = parts[0].split('>')[0].strip()
                            message_text = ':'.join(parts[1:]).strip()
                            
                            # Extract and process ACK ID if present
                            if "ack" in message_text:
                                parts = message_text.split("ack", 1)
                                if len(parts) == 2 and parts[1].isdigit():
                                    ack_id = parts[1]
                                    process_ack_id(from_callsign, ack_id)
                            # End RXd ACK ID for MSG Retries

                            # Check if the message contains "{"
                            if "{" in message_text:
                                message_id = message_text.split('{')[1]
                                
                            else:
                                message_id = '1'  

                            if ":" in message_text and APRS_CALLSIGN in message_text:
                                pass
                              
                                # Remove the first 11 characters from the message to exclude the "Callsign :" prefix
                                verbose_message = message_text[11:].split('{')[0].strip()

                                # Inside the receive_aprs_messages function
                                if private_mode:
                                    # Use regular expression to match main callsign and accept all SSIDs
                                    callsign_pattern = re.compile(r'^({})(-\d+)?$'.format('|'.join(map(re.escape, allowed_callsigns))))
                                    if not callsign_pattern.match(from_callsign):
                                        print("Unauthorized sender:", from_callsign)
                                        send_rej_message(from_callsign, message_id)
                                        continue  # Skip processing messages from unauthorized senders

                                # Display verbose message content
                                print("From: {}".format(from_callsign))
                                print("Message: {}".format(verbose_message))
                                print("Message ID: {}".format(message_id))
                                print(user_last_message_id)


                                # Check if the verbose message contains the desired format with a number or an alias
                                pattern = r'@(\d{10}|\w+) (.+)'
                                match = re.match(pattern, verbose_message)
                                                                    
                                # Send ACK
                                send_ack_message(from_callsign, message_id)                              
          
                                    
                                if match:
                                    recipient = match.group(1)
                                    
                                    # Update the dictionary with the last message number for the callsign
                                    
                                    # Use the reverse alias mapping to check if the sender's phone number has an associated alias
                                    alias = alias_map.get(recipient.lower())
                                    if alias:
                                        recipient = alias
                                    
                                    last_message_number[recipient.lower()] = from_callsign
                                    print ("To #", recipient)
                                    print ("From", from_callsign)
                                    print ("Dictionary", last_message_number)
                                    
                                    aprs_message = match.group(2)

                                    # Check if the recipient is a 10-digit number or an alias
                                    if recipient.isdigit():
                                        # Recipient is a 10-digit number
                                        phone_number = recipient
                                    else:
                                        # Recipient is an alias
                                        phone_number = find_phone_number_from_alias(recipient)


                                    if phone_number:
                                        # Get the last APRS message ID sent to this user
                                        last_message_id = user_last_message_id.get(from_callsign, 0)
                                        last_message_id += 1
                                        user_last_message_id[from_callsign] = last_message_id

                                    else:
                                        print("Recipient not found in alias map or not a 10-digit number: {}".format(recipient))                                
                                         
                                        

                                   # Check for duplicate messages
                                    if (aprs_message, message_id) in received_aprs_messages.get(from_callsign, set()):
                                        print("Duplicate message detected. Skipping SMS sending.")
                                        send_ack_message(from_callsign, message_id)                              

                                    else:
                                        # Mark the message as received
                                        received_aprs_messages.setdefault(from_callsign, set()).add((aprs_message, message_id))

                                        # Send SMS
                                        send_sms(TWILIO_PHONE_NUMBER, phone_number, from_callsign, aprs_message)
                                        
                                        # Add this line to mark the message ID as processed
                                        processed_message_ids.add(message_id)
                                                                       

                                    # Extract and process ACK ID if present
                                    if message_text.startswith("ack"):
                                        ack_id = message_text[3:]  # Remove the "ack" prefix
                                        process_ack_id(from_callsign, ack_id)


                                    pass
                                                                # Send ACK
                    # The last line might be an incomplete packet, so keep it in the buffer
                    buffer = lines[-1]

            except Exception as e:
                print("Error receiving APRS messages: {}".format(e))
                socket_ready = False  # Reset the socket_ready flag to indicate a disconnected state

                # Close the socket to release system resources
                if aprs_socket:
                    aprs_socket.close()

                time.sleep(1)  # Wait for a while before attempting to reconnect

        except Exception as e:
            print("Error in receive_aprs_messages:", str(e))


#Implementation for ack check with Message Retries #TODO
def process_ack_id(from_callsign, ack_id):
    print("Received ACK from {}: {}".format(from_callsign, ack_id))
    received_acks.setdefault(from_callsign, set()).add(ack_id)

    # Update your records or take any other necessary action

def send_keepalive():
    global socket_ready  # Declare that you're using the global variable

    while True:
        try:
            if socket_ready:
                # Send a keepalive packet to the APRS server
                keepalive_packet = '#\r\n'
                aprs_socket.sendall(keepalive_packet.encode())
                print("Sent keepalive packet.")
        except socket.error as e:
            print("Socket error while sending keepalive:", str(e))
            # Reestablish the connection to the APRS server
            establish_aprs_connection()
        except Exception as e:
            print("Error while sending keepalive:", str(e))
            # Reestablish the connection to the APRS server
            establish_aprs_connection()
        
        time.sleep(30)  # Send keepalive every 10 seconds

def send_beacon():
    global socket_ready  # Declare that you're using the global variable

    while True:
        try:
            if socket_ready:               
                # Send a keepalive packet to the APRS server
                beacon_packet = 'POSITION BEACON\r\n'
                status_beacon = 'STATUS BEACON\r\n'
                aprs_socket.sendall(beacon_packet.encode())
                aprs_socket.sendall(status_beacon.encode())

                print("Sent Beacon Packet.")
        except Exception as e:
            print("Error sending beacon:", str(e))
        time.sleep(600)  # Send beacon every 10 minutes
        
if __name__ == '__main__':
    print("APRS bot is running. Waiting for APRS messages...")

    # Start a separate thread for sending keepalive packets
    keepalive_thread = threading.Thread(target=send_keepalive)
    keepalive_thread.start()
    
    # Start a separate thread for sending keepalive packets
    beacon_thread = threading.Thread(target=send_beacon)
    beacon_thread.start()
    
    # Run the Flask web application in a separate thread to handle incoming SMS messages
    from threading import Thread
    webhook_thread = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000})
    webhook_thread.start()

    # Establish the initial connection to the APRS server
    establish_aprs_connection()  

    # Start listening for APRS messages
    receive_aprs_messages()
