import os
import json
import datetime
import pymongo
import requests
import boto3

# Initialize AWS Secrets Manager client
secrets_manager_client = boto3.client('secretsmanager')

# Get secrets from Secrets Manager
def get_secrets():
    secret_name = "dev_transactions_secret"  # Replace with your secret name from Secrets Manager
    try:
        get_secret_value_response = secrets_manager_client.get_secret_value(SecretId=secret_name)
        if 'SecretString' in get_secret_value_response:
            return json.loads(get_secret_value_response['SecretString'])
        else:
            # Handle binary secret if needed
            return json.loads(get_secret_value_response['SecretBinary'].decode('utf-8'))
    except Exception as e:
        print(f"Error retrieving secret: {e}")
        raise e

def lambda_handler(event, context):
    client = None # Initialize client to None
    try:
        secrets = get_secrets()
        mongodb_uri = secrets['MONGODB_URI']
        slack_webhook_url = secrets['SLACK_WEBHOOK_URL']

        # Connect to MongoDB
        # Note: The database name 'bill_vending' should ideally be part of your MONGODB_URI in Secrets Manager
        # e.g., mongodb+srv://<username>:<password>@cluster0.w5zljvq.mongodb.net/bill_vending?retryWrites=true&w=majority
        client = pymongo.MongoClient(mongodb_uri)
        db = client['bill_vending'] # Explicitly specify the database name
        
        # Specify the correct collection name
        transactions_collection = db['power_transaction_items']

        # Calculate the date range for today (start of day to end of day)
        # We need to format this to match the 'YYYY-MM-DD HH:MM:SS' string format in 'date_created'
        now_utc = datetime.datetime.utcnow() # Use UTC for consistency if your DB dates are UTC
        
        # If your 'date_created' field stores dates in local time (e.g., EDT)
        # you might need to adjust 'today' to your specific timezone or just use UTC and
        # ensure your cron triggers match the UTC time. For simplicity, we'll assume
        # 'date_created' corresponds to UTC or you're fine with UTC-based summaries.
        
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1) # End of today

        # Format dates to match the string format in MongoDB: "YYYY-MM-DD HH:MM:SS"
        # The 'date_created' in your sample data does not have milliseconds/microseconds, so we truncate.
        today_start_str = today_start.strftime("%Y-%m-%d %H:%M:%S")
        today_end_str = today_end.strftime("%Y-%m-%d %H:%M:%S")

        print(f"Fetching transactions from {today_start_str} to {today_end_str}")

        # Aggregate transactions for today
        pipeline = [
            {
                '$match': {
                    'date_created': {
                        '$gte': today_start_str, # Use string comparison for 'date_created'
                        '$lte': today_end_str    # Use string comparison for 'date_created'
                    }
                }
            },
            {
                '$group': {
                    '_id': None,
                    'totalTransactions': {'$sum': 1},
                    'totalSuccessfulAmount': {
                        '$sum': {
                            '$cond': [
                                # Assuming 'fulfilled' is the success status
                                {'$eq': ['$status', 'fulfilled']},
                                # Convert 'amount' from string to float before summing
                                {'$toDouble': '$amount'},
                                0
                            ]
                        }
                    },
                    'totalFailedTransactions': {
                        '$sum': {
                            '$cond': [
                                # Assuming 'failed' (or not 'fulfilled') implies failure
                                {'$ne': ['$status', 'fulfilled']},
                                1,
                                0
                            ]
                        }
                    }
                }
            }
        ]
        
        # Execute aggregation
        result = list(transactions_collection.aggregate(pipeline))

        summary_message = "Daily Transaction Summary:\n"
        # We also need to specify the timezone for the summary message
        # For EDT, which is UTC-4, we can convert.
        # Alternatively, you could fetch current local time for display.
        
        # Example to display time in a specific timezone (e.g., EDT)
        # You'd need to install 'pytz' if you want robust timezone handling,
        # but for simple display, direct offset calculation for a fixed timezone works.
        # For a fixed time like EDT (UTC-4)
        edt_offset = datetime.timedelta(hours=-4)
        local_today_start = today_start + edt_offset
        
        if result and result[0].get('totalTransactions', 0) > 0: # Check if there are any transactions
            data = result[0]
            summary_message += f"Date: {local_today_start.strftime('%Y-%m-%d')} (EDT)\n"
            summary_message += f"Total Transactions: {data.get('totalTransactions', 0)}\n"
            summary_message += f"Total Successful Amount: ${data.get('totalSuccessfulAmount', 0):,.2f}\n"
            summary_message += f"Total Failed Transactions: {data.get('totalFailedTransactions', 0)}\n"
        else:
            summary_message += f"No transactions found for {local_today_start.strftime('%Y-%m-%d')} (EDT).\n"

        # Prepare Slack payload
        slack_payload = {
            "text": summary_message
        }

        # Send to Slack
        response = requests.post(
            slack_webhook_url,
            data=json.dumps(slack_payload),
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status() # Raise an exception for HTTP errors

        print(f"Slack message sent successfully: {summary_message}")

        return {
            'statusCode': 200,
            'body': json.dumps('Transaction summary sent to Slack successfully!')
        }

    except Exception as e:
        print(f"Error processing transaction summary: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }
    finally:
        if client: # Ensure client is defined before trying to close
            client.close() # Close MongoDB connection
