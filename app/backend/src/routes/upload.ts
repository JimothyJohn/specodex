/**
 * Upload route — handles PDF submission for extraction queue.
 *
 * Flow:
 * 1. Client POST /api/upload with metadata (product_name, manufacturer, etc.)
 * 2. Backend creates DynamoDB record (status=queued) + returns presigned S3 PUT URL
 * 3. Client PUTs the PDF directly to S3 using the presigned URL
 * 4. Local processor (`./Quickstart process`) picks up queued items and extracts
 *
 * Available in both public and admin mode — uploading only queues work,
 * it doesn't modify existing data.
 */

import { Router, Request, Response } from 'express';
import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { DynamoDBService } from '../db/dynamodb';
import { v4 as uuidv4 } from 'uuid';
import config from '../config';
import { safeLog } from '../util/log';

const router = Router();
const db = new DynamoDBService({ tableName: config.dynamodb.tableName });

const s3 = new S3Client({ region: config.aws.region });
const BUCKET = process.env.UPLOAD_BUCKET || `datasheetminer-uploads-${config.stage}-${config.aws.accountId}`;

/**
 * POST /api/upload
 * Create a queued datasheet record and return a presigned URL for the PDF.
 *
 * Body: { product_name, manufacturer, product_type, pages?, filename }
 * Returns: { upload_url, datasheet_id, s3_key }
 */
router.post('/', async (req: Request, res: Response): Promise<void> => {
  try {
    const { product_name, manufacturer, product_type, pages, filename } = req.body;

    if (!product_name || !manufacturer || !product_type || !filename) {
      res.status(400).json({
        success: false,
        error: 'Missing required fields: product_name, manufacturer, product_type, filename',
      });
      return;
    }

    if (!filename.toLowerCase().endsWith('.pdf')) {
      res.status(400).json({
        success: false,
        error: 'Only PDF files are accepted',
      });
      return;
    }

    const datasheetId = uuidv4();
    const s3Key = `queue/${datasheetId}/${filename}`;

    // Create DynamoDB record with queued status
    const datasheet = {
      datasheet_id: datasheetId,
      product_type,
      product_name,
      manufacturer,
      pages: pages || undefined,
      url: `s3://${BUCKET}/${s3Key}`,
      status: 'queued',
      uploaded_at: new Date().toISOString(),
    };

    const created = await db.create(datasheet);
    if (!created) {
      res.status(500).json({ success: false, error: 'Failed to create datasheet record' });
      return;
    }

    // Generate presigned PUT URL (valid for 15 minutes)
    const command = new PutObjectCommand({
      Bucket: BUCKET,
      Key: s3Key,
      ContentType: 'application/pdf',
    });
    const uploadUrl = await getSignedUrl(s3, command, { expiresIn: 900 });

    // s3Key embeds the user-supplied filename — strip CR/LF inline before
    // logging (CodeQL js/log-injection barrier; see util/log.ts).
    console.log(`[upload] Queued datasheet ${datasheetId} (key=${safeLog(s3Key.replace(/\r|\n/g, ''))})`);

    res.status(201).json({
      success: true,
      data: {
        datasheet_id: datasheetId,
        s3_key: s3Key,
        upload_url: uploadUrl,
      },
    });
  } catch (error) {
    console.error('Error creating upload:', error);
    res.status(500).json({ success: false, error: 'Failed to create upload' });
  }
});

export default router;
