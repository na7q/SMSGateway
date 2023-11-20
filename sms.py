import socket
import re
from flask import Flask, request, jsonify
from twilio.rest import Client
import time
import threading
import setproctitle
import json

# Set the custom process name
setproctitle.setproctitle("sms")

app = Flask(__name__)

# Set the expiration time in seconds (e.g., 90 seconds)
MESSAGE_EXPIRATION_TIME = 3600

# Set this variable to enable/disable private mode
private_mode = False  # Change this value as needed

# List of callsigns allowed to send messages if private_mode is TRUE. Accepts ALL SSIDs for a CALLSIGN listed.
allowed_callsigns = ['CALLSIGN0', 'CALLSIGN1', 'CALLSIGN2']  # Add more callsigns as needed

tocall = 'APOSMS'
user_callsign = 'YOUR_CALLSIGN'

# Twilio credentials
TWILIO_ACCOUNT_SID = 'SID'
TWILIO_AUTH_TOKEN = 'AUTH'
TWILIO_PHONE_NUMBER = '+NUMBER'  # Your Twilio phone number
TWILIO_PHONE_NUMBER_UK = '+UKNUMBER'  #UK SUPPORT

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

def handle_alias_update(from_callsign, verbose_message):
    global alias_map, reverse_alias_map  # Access the global dictionaries

    # Extract alias information from the received message
    alias_info = verbose_message.split('#alias', 1)[1].strip()  # Remove the '#alias' prefix and trim whitespace

    # Split the alias information into parts
    alias_parts = alias_info.split()

    if len(alias_parts) == 3:
        action, alias_name, alias_phone = alias_parts
        alias_name = alias_name.lower()

        # Ensure the action is valid
        if action in ("#add", "#remove"):
        
            # Check if the alias phone is either 10 or 12 digits long #UK Support
            if len(alias_phone) not in {10, 12} or not alias_phone.isdigit():
                print("Invalid alias phone number. It must be either exactly 10 or 12 digits long.")
                return
                  
            # Extract the SSID from the from_callsign
            from_callsign_parts = from_callsign.split('-')
            if len(from_callsign_parts) == 2:
                from_callsign_strip = from_callsign_parts[0]
            else:
                from_callsign_strip = from_callsign  # No SSID found

            if from_callsign_strip in alias_map:
                existing_aliases = alias_map[from_callsign_strip]

                if action == "#add":
                    # Adding an alias
                    if alias_name in existing_aliases:
                        existing_phone = existing_aliases[alias_name]
                        if existing_phone != alias_phone:
                            # Alias name is found, but with a different phone number, update both name and phone
                            existing_aliases[alias_name] = alias_phone
                                                                                
                    elif alias_phone in existing_aliases.values():
                        # Alias phone number is found, update the associated alias name
                        for name, phone in existing_aliases.items():
                            if phone == alias_phone:
                                existing_aliases[alias_name] = alias_phone
                                del existing_aliases[name]  # Remove the old alias name
                                break  # Stop searching after the first match is found

                    else:
                        # Neither the alias name nor the alias phone is found, add the new alias
                        existing_aliases[alias_name] = alias_phone

                elif action == "#remove":
                    # Removing an alias
                    if alias_name in existing_aliases:
                        if existing_aliases[alias_name] == alias_phone:
                            # Check if the provided alias and phone match the existing alias
                            alias_map[from_callsign_strip].pop(alias_name)
                            if not alias_map[from_callsign_strip]:
                                # If there are no more aliases for this callsign, remove the callsign entry
                                alias_map.pop(from_callsign_strip)
                    else:
                        print("Alias not found for removal:", alias_name, alias_phone)
            else:
                # If the callsign is not in the alias map, create a new entry
                if action == "#add":
                    alias_map[from_callsign_strip] = {alias_name: alias_phone}

            # Update the reverse_alias_map to reflect the changes
            reverse_alias_map = generate_reverse_alias_map(alias_map)

            # Save the updated alias map to a file
            save_alias_map_to_file(alias_map, alias_map_filename)

            # Print a message indicating the update
            print("Alias map updated:")
            print(alias_map)
        else:
            print("Invalid action:", action)
    else:
        print("Invalid alias information:", alias_info)

def save_alias_map_to_file(alias_map, filename):
    # Save the alias map to the specified file in JSON format with proper formatting
    with open(filename, 'w') as file:
        formatted_json = json.dumps(alias_map, indent=4)
        file.write(formatted_json)

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

def send_aprs_messages(callsign, from_phone_number, sender_phone_number, aprs_message, last_message_id):
    #New Chunk Method
    chunk_size = 67

    # Additional characters for portion information
    portion_info_chars = 4

    # Calculate the constant value considering portion information
    constant_value = chunk_size - len(sender_phone_number) - 2 - portion_info_chars

    # Split the APRS message into chunks of 67 characters
    message_chunks = [aprs_message[i:i + constant_value] for i in range(0, len(aprs_message), constant_value)]
    #End Chunk Method

    #New Portion Calc
    total_chunks = len(message_chunks)

    # Get the last APRS message ID sent to this user
    last_message_id = user_last_message_id.get(from_phone_number, 0)
    user_last_message_id[from_phone_number] = last_message_id


    # Initialize a separate counter variable for the message ID within the loop
    current_message_id = last_message_id

    for i, chunk in enumerate(message_chunks):
        # Calculate portion information
        portion_info = " {}/{}".format(i + 1, total_chunks) if total_chunks > 1 else ""
        
        print("Chunks", len(message_chunks))
        # Format the APRS packet and send it to the APRS server
        aprs_packet = format_aprs_packet(callsign, "@{}{} {}{}".format(sender_phone_number, portion_info, chunk, "{" + str(current_message_id)))
        print("chunks ID: ", current_message_id)
        print(aprs_packet)
        aprs_socket.sendall(aprs_packet.encode())

        # Not a good delay, maybe seek alternative options.
        time.sleep(10)  # Sleeping here allows time for incoming ack before retry
        print("sleep ID: ", current_message_id)
        retry_count = 0
        ack_received = False

        while retry_count < MAX_RETRIES and not ack_received:
            if str(current_message_id) in received_acks.get(callsign, set()):
                print("Message ACK received. No further retries needed.")
                ack_received = True
                retry_count = 0
                received_acks.get(callsign, set()).discard(str(current_message_id))
            else:
                print("ACK not received. Retrying in {} seconds.".format(RETRY_INTERVAL))
                aprs_socket.sendall(aprs_packet.encode())
                retry_count += 1
                time.sleep(RETRY_INTERVAL)

        if ack_received:
            print("ACK received during retries. No further retries needed.")
        elif retry_count >= MAX_RETRIES:
            print("Max retries reached. No ACK received for the message.")
        
        # Increment the counter variable for the next chunk
        current_message_id += 1

    # Update the last_message_id outside the loop
    last_message_id += len(message_chunks) - 1
    
    user_last_message_id[from_phone_number] = last_message_id

    print("Last ID: ", last_message_id)
    print("Next ID: ", current_message_id)
    print(user_last_message_id)


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


def send_sms_uk(twilio_phone_number_uk, to_phone_number, from_callsign, body_message): #UK SUPPORT
    # Initialize the Twilio client
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        # Send SMS using the Twilio API
        message = client.messages.create(
            body="@{} {}".format(from_callsign, body_message),
            from_=twilio_phone_number_uk,
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

def load_alias_map_from_file(filename):
    try:
        with open(filename, 'r') as file:
            alias_map = json.load(file)
            return alias_map
    except (FileNotFoundError, json.JSONDecodeError):
        return {}  # Return an empty dictionary if the file is not found or cannot be parsed

# Define the filename for the alias map file
alias_map_filename = '/root/app/sms_map.json'

# Load the alias map from the file
alias_map = load_alias_map_from_file(alias_map_filename)

# Create a new dictionary to store the reverse mapping of phone numbers and aliases to callsigns
def generate_reverse_alias_map(alias_map):
    reverse_alias_map = {}
    for callsign, aliases_and_numbers in alias_map.items():
        reverse_alias_map[callsign] = {}
        for alias, phone_number in aliases_and_numbers.items():
            reverse_alias_map[callsign][phone_number] = alias
    return reverse_alias_map

# Whenever you update the alias map, you can regenerate the reverse_alias_map
# For example, after updating the alias map with new data, call this function
reverse_alias_map = generate_reverse_alias_map(alias_map)


def extract_sender_phone_number(from_phone_number):
    # Extract the phone number from the sender's phone number based on the prefix
    if from_phone_number.startswith('+1'):
        return from_phone_number[2:]
    elif from_phone_number.startswith('+44'):
        return from_phone_number[1:]
    else:
        return from_phone_number[-10:]
        

def is_message_expired(timestamp):
    return time.time() - timestamp > MESSAGE_EXPIRATION_TIME


@app.route('/sms', methods=['POST'])
def receive_sms():
    global last_message_number #Questioning this. Consider options later.
    # Parse the incoming SMS message
    data = request.form
    from_phone_number = data['From']
    body_message = data['Body']
    print (body_message)
    
    if body_message.startswith('@'):
        parts = body_message.split(' ', 1)
        if len(parts) == 2:
            # Extract the phone number from the sender's phone number
            sender_phone_number = extract_sender_phone_number(from_phone_number)           
            callsign = parts[0][1:].upper()
            aprs_message = parts[1]
            print(callsign)

            last_message_number[sender_phone_number] = callsign #Questioning this. Consider options later.
            print(last_message_number)


            # Get the last APRS message ID sent to this user
            last_message_id = user_last_message_id.get(from_phone_number, 0)
            last_message_id += 1
            user_last_message_id[from_phone_number] = last_message_id
            print("RX SMS ID: ", last_message_id)
            print("RX USR ID: ", user_last_message_id)


            # Extract the SSID from the from_callsign
            from_callsign_parts = callsign.split('-')            
            if len(from_callsign_parts) == 2:
                from_callsign_strip = from_callsign_parts[0]
            else:
                from_callsign_strip = callsign  # No SSID found
                print("No SSID Found")
            
            # Use the reverse alias mapping to check if the sender's phone number has an associated alias
            alias = reverse_alias_map.get(from_callsign_strip, {}).get(sender_phone_number.lower())
            if alias:
                sender_phone_number = alias

            # Format and send APRS packets
            send_aprs_messages(callsign, from_phone_number, sender_phone_number, aprs_message, last_message_id)
            
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'Invalid SMS format'})
    else:
    
        print ("no callsign found")
        
        sender_phone_number = extract_sender_phone_number(from_phone_number)           
        callsign = last_message_number.get(sender_phone_number, None)

        print("From:", from_phone_number[-10:])
        print("From Full:", from_phone_number)
        print("Dictionary:", last_message_number)
        print("Callsign Found:", callsign)

        if callsign:
            # Extract the APRS message content
            aprs_message = body_message

            # Get the last APRS message ID sent to this user
            last_message_id = user_last_message_id.get(from_phone_number, 0)
            last_message_id += 1
            user_last_message_id[from_phone_number] = last_message_id

            # Extract the SSID from the from_callsign
            from_callsign_parts = callsign.split('-')
            if len(from_callsign_parts) == 2:
                from_callsign_strip = from_callsign_parts[0]
            else:
                from_callsign_strip = callsign  # No SSID found
            
            # Use the reverse alias mapping to check if the sender's phone number has an associated alias
            alias = reverse_alias_map.get(from_callsign_strip, {}).get(sender_phone_number.lower())
            if alias:
                sender_phone_number = alias            

            # Format and send APRS packets
            send_aprs_messages(callsign, from_phone_number, sender_phone_number, aprs_message, last_message_id)


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
            login_str = 'user {} pass {} vers SMS-Gateway 1.4 Beta\r\n'.format(APRS_CALLSIGN, APRS_PASSCODE)
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
    global socket_ready, last_message_number, alias_map   # Declare that you're using the global variable

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

                            # Check if verbose_message starts with ":SMS:" (or the appropriate APRS_CALLSIGN)
                            if message_text.startswith(":{}".format(APRS_CALLSIGN)):
     
                                # Check if the message contains "{"
                                if "{" in message_text[-6:]:
                                    message_id = message_text.split('{')[1]
                                        
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

                                    # Initialize match
                                    match = None

                                    # Check if the verbose message contains the desired format with a number or an alias
                                    #alias_pattern = r'#alias (.+)'
                                    alias_pattern = re.compile(r'#alias (.+)', re.IGNORECASE)

                                    alias_match = re.search(alias_pattern, verbose_message)
                                    print("alias pattern")
                                    if alias_match:
                                        print("did we make it to 1")
                                        # Call a function to handle alias updates
                                        handle_alias_update(from_callsign, verbose_message.lower()) #added lower
                                    else:
                                        pattern = r'@(\d{10}|\w+) (.+)'
                                        match = re.match(pattern, verbose_message)

                                                                       
                                    # Send ACK
                                    send_ack_message(from_callsign, message_id)                              
                                    print("did we send this ack?")
                                        
                                    if match:
                                        recipient = match.group(1)
                                        
                                        # Update the dictionary with the last message number for the callsign
                                        
                                        # Extract the SSID from the from_callsign
                                        from_callsign_parts = from_callsign.split('-')
                                        if len(from_callsign_parts) == 2:
                                            from_callsign_strip = from_callsign_parts[0]
                                        else:
                                            from_callsign_strip = from_callsign  # No SSID found

                                        # Use the reverse alias mapping to check if the sender's phone number has an associated alias
                                        alias = alias_map.get(from_callsign_strip, {}).get(recipient.lower())
                                        if alias:
                                            recipient = alias
                                            print("Recipient:", recipient)

                                        
                                        #last_message_number[recipient.lower()] = from_callsign
                                        print ("To #", recipient)
                                        print ("From", from_callsign)
                                        print ("Dictionary", last_message_number)
                                        
                                        aprs_message = match.group(2)

                                        # Check if the recipient is a 10-digit number or an alias
                                        if recipient.isdigit():
                                            # Recipient is a 10-digit number
                                            phone_number = recipient
                                            last_message_number[recipient.lower()] = from_callsign

                                        else:
                                            # Recipient is an alias
                                            phone_number = alias_map.get(from_callsign, {}).get(recipient)
                                            print("phone", phone_number)

                                        if phone_number:
                                            # Get the last APRS message ID sent to this user
                                            last_message_id = user_last_message_id.get(from_callsign, 0)
                                            last_message_id += 1
                                            user_last_message_id[from_callsign] = last_message_id

                                        if not phone_number:
                                            print("Recipient not found in alias map or not a 10 or 12 digit number: {}".format(recipient))
                                            continue  # Skip processing the current message and move on to the next one in the loop
     
                                        # Check for duplicate messages
                                        messages_for_callsign = received_aprs_messages.get(from_callsign, [])
                                        current_time = time.time()
                                        
                                        
                                        # Check if the message has been received in the last 90 seconds
                                        if any(not is_message_expired(stored_timestamp) and stored_message_id == message_id for stored_message, stored_message_id, stored_timestamp in messages_for_callsign):
                                            print("Message received in the last 3600 seconds. Skipping further processing.")                                        

                                        #UK SUPPORT
                                        else:
                                            # Mark the message as received
                                            received_aprs_messages.setdefault(from_callsign, []).append((aprs_message, message_id, current_time))
                                            print(received_aprs_messages)
                                            
                                            if len(phone_number) == 12 and phone_number.startswith("44"):
                                                # UK phone number format: 12 digits and starts with "44"
                                                send_sms_uk(TWILIO_PHONE_NUMBER_UK, phone_number, from_callsign, aprs_message)
                                            else:
                                                # Default behavior
                                                send_sms(TWILIO_PHONE_NUMBER, phone_number, from_callsign, aprs_message)

                                            # Add this line to mark the message ID as processed
                                            processed_message_ids.add(message_id)
                                            print("Process MSG ID")                                    

                                        # Extract and process ACK ID if present
                                        if message_text.startswith("ack"):
                                            ack_id = message_text[3:]  # Remove the "ack" prefix
                                            process_ack_id(from_callsign, ack_id)
                                            print("Process ACK ID")

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
                beacon_packet = '{}>{}:Your Position {}\r\n'.format(APRS_CALLSIGN, tocall, user_callsign)
                status_beacon = '{}>{}:>Status Beacon\r\n'.format(APRS_CALLSIGN, tocall)
                aprs_socket.sendall(beacon_packet.encode())
                aprs_socket.sendall(status_beacon.encode())

                print("Sent Beacon Packet.")
        except Exception as e:
            print("Error sending beacon:", str(e))
        time.sleep(600)  # Send beacon every 10 minutes
        
if __name__ == '__main__':
    print("APRS bot is running. Waiting for APRS messages...")

    # Establish the initial connection to the APRS server
    establish_aprs_connection()  

    # Start a separate thread for sending keepalive packets
    keepalive_thread = threading.Thread(target=send_keepalive)
    keepalive_thread.start()
    
    # Start a separate thread for sending keepalive packets
    beacon_thread = threading.Thread(target=send_beacon)
    beacon_thread.start()
    
    # Run the Flask web application in a separate thread to handle incoming SMS messages
    from threading import Thread
    webhook_thread = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 12345})
    webhook_thread.start()

    # Start listening for APRS messages
    receive_aprs_messages()
