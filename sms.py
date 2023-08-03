import socket
import re
from flask import Flask, request, jsonify
from twilio.rest import Client

app = Flask(__name__)

# Set this variable to enable/disable private mode
private_mode = False  # Change this value as needed

# List of callsigns allowed to send messages if private_mode is TRUE. Accepts ALL SSIDs for a CALLSIGN listed.
allowed_callsigns = ['CALLSIGN0', 'CALLSIGN1', 'CALLSIGN2']  # Add more callsigns as needed

# Twilio credentials
TWILIO_ACCOUNT_SID = 'SID'
TWILIO_AUTH_TOKEN = 'TOKEN'
TWILIO_PHONE_NUMBER = '+NUMBER'  # Your Twilio phone number

# APRS credentials
APRS_CALLSIGN = 'CALLSIGN'
APRS_PASSCODE = 'PASSCODE'
APRS_SERVER = 'roate.aprs2.net'
APRS_PORT = 14580

# Initialize the socket
aprs_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Dictionary to store the last received APRS message ID for each user
user_last_message_id = {}


def send_ack_message(sender, message_id):
    ack_message = 'ack{}'.format(message_id)
    sender_length = len(sender)
    spaces_after_sender = ' ' * max(0, 9 - sender_length)
    ack_packet_format = '{}>APRS::{}{}:{}\r\n'.format(APRS_CALLSIGN, sender, spaces_after_sender, ack_message)
    ack_packet = ack_packet_format.encode()
    aprs_socket.sendall(ack_packet)
    print("Sent ACK to {}: {}".format(sender, ack_message))
    print("Outgoing ACK packet: {}".format(ack_packet.decode()))


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
    spaces_after_sender = ' ' * max(0, 9 - sender_length)
    aprs_packet_format = '{}>NA7Q::{}{}:{}\r\n'.format(APRS_CALLSIGN, callsign, spaces_after_sender, message)
    return aprs_packet_format

# Dictionary to store the mapping of aliases (callsigns) to phone numbers
alias_map = {
    'alias1': '1234567890',  # Replace 'alias1' with the desired alias and '1234567890' with the corresponding phone number.
    'alias2': '0987654321',  # Add more entries as needed for other aliases and phone numbers.
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

    # If the message is in the correct format, the function extracts the callsign and APRS message content from the SMS body.
    if body_message.startswith('@'):
        parts = body_message.split(' ', 1)
        if len(parts) == 2:
            # Extract the 10-digit phone number from the sender's phone number
            sender_phone_number = from_phone_number[-10:]
            callsign = parts[0][1:]
            aprs_message = parts[1]

            # Get the last APRS message ID sent to this user
            last_message_id = user_last_message_id.get(from_phone_number, 0)

            # Increment the message ID to avoid duplicate messages
            last_message_id += 1
            user_last_message_id[from_phone_number] = last_message_id

            # Use the reverse alias mapping to check if the sender's phone number has an associated alias
            alias = reverse_alias_map.get(sender_phone_number.lower())
            if alias:
                sender_phone_number = alias
            # If an alias is found, use it; otherwise, use the phone number itself as the alias
            if alias:
                sender_phone_number = alias

            # Format the APRS packet and send it to the APRS server
            aprs_packet = format_aprs_packet(callsign, "@{} {}".format(sender_phone_number, aprs_message + "{" + str(last_message_id)))
            aprs_socket.sendall(aprs_packet.encode())
            print("Sent APRS message to {}: {}".format(callsign, aprs_message))
            print("Outgoing APRS packet: {}".format(aprs_packet.strip()))

            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'Invalid SMS format'})
    else:
        return jsonify({'status': 'error', 'message': 'SMS does not start with "@" symbol'})


def receive_aprs_messages():
    # Connect to the APRS server
    aprs_socket.connect((APRS_SERVER, APRS_PORT))
    print("Connected to APRS server with callsign: {}".format(APRS_CALLSIGN))

    # Send login information with APRS callsign and passcode
    login_str = 'user {} pass {} vers SMS-Gateway 0.1b\r\n'.format(APRS_CALLSIGN, APRS_PASSCODE)
    aprs_socket.sendall(login_str.encode())
    print("Sent login information.")


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

                    # Check if the message contains "{"
                    if "{" in message_text:
                        message_id = message_text.split('{')[1].strip('}')
                        
                        # Remove the first 11 characters from the message to exclude the "Callsign :" prefix
                        verbose_message = message_text[11:].split('{')[0].strip()

                        # If private_mode is enabled, check against allowed_callsigns; otherwise, process normally
                        if private_mode:
                            # Use regular expression to match main callsign and accept all SSIDs
                            callsign_pattern = re.compile(r'^({})(-\d+)?$'.format('|'.join(map(re.escape, allowed_callsigns))))
                            if not callsign_pattern.match(from_callsign):
                                print("Unauthorized sender:", from_callsign)
                                send_ack_message(from_callsign, message_id)  # Send ACK for unauthorized sender
                                continue  # Skip processing messages from unauthorized senders                        

                        # Display verbose message content
                        print("From: {}".format(from_callsign))
                        print("Message: {}".format(verbose_message))
                        print("Message ID: {}".format(message_id))

                        # Check if the verbose message contains the desired format with a number or an alias
                        pattern = r'@(\d{10}|\w+) (.+)'
                        match = re.match(pattern, verbose_message)
                                                            
                        # Send ACK
                        send_ack_message(from_callsign, message_id)
                            
                        if match:
                            recipient = match.group(1)
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

                                # Send SMS
                                send_sms(TWILIO_PHONE_NUMBER, phone_number, from_callsign, aprs_message)

                            else:
                                print("Recipient not found in alias map or not a 10-digit number: {}".format(recipient))

                            pass
                                                        # Send ACK
            # The last line might be an incomplete packet, so keep it in the buffer
            buffer = lines[-1]

    except Exception as e:
        print("Error receiving APRS messages: {}".format(e))

    finally:
        # Close the socket connection when done
        aprs_socket.close()




if __name__ == '__main__':
    print("APRS bot is running. Waiting for APRS messages...")
    
    # Run the Flask web application in a separate thread to handle incoming SMS messages
    from threading import Thread
    webhook_thread = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000})
    webhook_thread.start()

    # Start listening for APRS messages
    receive_aprs_messages()
