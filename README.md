# SMSGateway
APRS &lt;> SMS Gateway

This is a python based bot for running a bidirectional APRS <> SMS Gateway.<br>

Using the Twilio Phone and API services you can setup your own basic gateway.<br><br>


Python3.9 is recommended. Will work with older versions.<br><br>

Install Dependencies:<br>
pip install twilio flask<br><br>

Terminal Command:<br>
nohup python3 /root/app/sms.py > /dev/null 2>&1 &<br><br>

Not supported: <br>
Checking Missed SMS Messages via APRS<br>
User level alias mapping.
<br>
TO DO:<br>
Store messages not acked by APRS user.<br>
Check missed messages for APRS user.<br>
Add ack resend for duplicate messages<br>

