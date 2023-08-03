# SMSGateway
APRS &lt;> SMS Gateway

This is a python based bot for running a bidirectional APRS <> SMS Gateway.<br>

Using the Twilio Phone and API services you can setup your own basic gateway.<br><br>

Not supported: <br>
Checking Missed SMS Messages via APRS<br>
SMS to APRS Message Retries<br>
Duplicate Message Checking, APRS or SMS
SMS to APRS without Explicit Callsign<br><br>

Install Dependencies:<br>
pip install twilio flask<br><br>

Terminal Command:<br>
nohup python3 /root/app/sms.py > /dev/null 2>&1 &<br><br>
