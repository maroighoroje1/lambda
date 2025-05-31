# my_lambda_summary_code/app.py
import os
import json
import logging
from datetime import datetime, timedelta, timezone
import requests
import pymongo
import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO').upper())

ssm_client = boto3.client('ssm') # Initialize SSM client

# Define environment variables that will hold the Parameter Store names
# You'll set these in template.yaml in the next step
MONGODB_URI_PARAM = os.environ.get('MONGODB_URI_PARAM_NAME')
SLACK_WEBHOOK_PARAM = os.environ.get('SLACK_WEBHOOK_PARAM_NAME')

def get_parameter(param_name, decrypt=False):
    """Helper to retrieve a parameter from Parameter Store."""
    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=decrypt)
        return response['Parameter']['Value']
    except ssm_client.exceptions.ParameterNotFound:
        logger.error(f"Parameter '{param_name}' not found.")
        raise
    except Exception as e:
        logger.error(f"Error retrieving parameter '{param_name}': {e}")
        raise

# ... (rest of your existing get_time_range function and other helper functions)

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    mongodb_uri = None
    slack_webhook_url = None

    try:
        if MONGODB_URI_PARAM:
            mongodb_uri = get_parameter(MONGODB_URI_PARAM, decrypt=True) # Decrypt for SecureString
        if SLACK_WEBHOOK_PARAM:
            slack_webhook_url = get_parameter(SLACK_WEBHOOK_PARAM, decrypt=True) # Decrypt for SecureString

        if not mongodb_uri or not slack_webhook_url:
            logger.error("Missing MongoDB URI or Slack Webhook URL from Parameter Store.")
            return {'statusCode': 500, 'body': 'Configuration Error: Missing credentials.'}

        # Determine time range for transaction summary
        event_time_from_eventbridge = event.get('time')
        if not event_time_from_eventbridge:
            logger.error("EventBridge 'time' field not found in event. Using current UTC time as fallback.")
            event_time_from_eventbridge = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        start_time, end_time, summary_period_desc = get_time_range(event_time_from_eventbridge)

        # --- MongoDB Connection and Data Fetching ---
        summary_message = "" # Initialize summary message
        try:
            client = pymongo.MongoClient(mongodb_uri)
            db = client.get_default_database()
            transactions_collection = db['transactions'] # <<< IMPORTANT: Change to your actual collection name

            # ... (rest of your MongoDB query and summary calculation logic)
            # ... (ensure your timestamp field name, e.g., 'createdAt', is correct in the query)
            # ... (ensure your success status, e.g., 'SUCCESS', is correct)

            summary_message = (
                f"📊 *Bill Payment Dev Environment Summary for {summary_period_desc}*\n"
                f"*(Time Range: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} to {end_time.strftime('%Y-%m-%d %H:%M:%S UTC')})*\n"
                f"• Total Transactions: `{total_transactions}`\n"
                f"• Total Amount Processed: `₦{total_amount_processed:,.2f}`\n"
                f"• Successful Transactions: `{successful_transactions}`\n"
                f"• Failed Transactions: `{failed_transactions}`"
            )
            client.close()

        except pymongo.errors.ConnectionFailure as e:
            logger.error(f"MongoDB Connection Error: {e}")
            summary_message = f"❌ *MongoDB Connection Error for Dev Summary*: Unable to connect. Error: {e}"
        except Exception as e:
            logger.error(f"Error processing transactions: {e}", exc_info=True)
            summary_message = f"❌ *Error generating Dev Summary*: An unexpected error occurred. Error: {e}"

        # --- Send Summary to Slack ---
        try:
            slack_payload = {
                "text": summary_message,
                "username": "Bill Payment Dev Bot",
                "icon_emoji": ":robot_face:"
            }
            response = requests.post(slack_webhook_url, data=json.dumps(slack_payload), headers={'Content-Type': 'application/json'})
            response.raise_for_status()
            logger.info(f"Summary sent to Slack. Status Code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending summary to Slack: {e}", exc_info=True)
            return {'statusCode': 500, 'body': json.dumps(f'Failed to send summary to Slack: {e}')}

    except Exception as e:
        logger.error(f"Critical error during Lambda execution: {e}", exc_info=True)
        return {'statusCode': 500, 'body': json.dumps(f'Critical error: {e}')}

    return {'statusCode': 200, 'body': json.dumps('Transaction summary process completed.')}
