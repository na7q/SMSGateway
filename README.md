# SMSGateway
APRS &lt;> SMS Gateway

This is a python based bot for running a bidirectional APRS <> SMS Gateway.<br>

Using the Twilio Phone and API services you can setup your own basic gateway.<br><br>


Python3.9 is recommended. Will work with older versions.<br><br>

Install Dependencies:<br>
pip install twilio flask<br><br>

Terminal Command:<br>
nohup python3 /root/app/sms.py > /dev/null 2>&1 &<br><br>

Features:<br>
SMS to APRS Message Retries<br>
Duplicate Message Checking of APRS Messages<br>
SMS to APRS retry when APRS user didn't ack message.<br><br>

Not supported: <br>
Checking Missed SMS Messages via APRS<br>
SMS to APRS without Explicit Callsign<br><br>

TO DO:<br>
Store messages not acked by APRS user.<br>
Check missed messages for APRS user.<br>
Ability to send SMS to APRS by automatically using last call to phone number.<br>
