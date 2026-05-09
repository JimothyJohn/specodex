/**
 * DynamoDB client for CRUD operations on products.
 * This module mirrors the functionality of specodex/db/dynamo.py
 */

import {
  DynamoDBClient,
  GetItemCommand,
  PutItemCommand,
  QueryCommand,
  QueryCommandOutput,
  DeleteItemCommand,
  BatchWriteItemCommand,
  ScanCommand,
  ScanCommandOutput,
  AttributeValue,
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';
import { Product, ProductType, Datasheet, Motor, Drive, Manufacturer } from '../types/models';
import { VALID_PRODUCT_TYPES, formatDisplayName } from '../config/productTypes';
import { safeLog } from '../util/log';

export interface DynamoDBConfig {
  tableName: string;
  region?: string;
}

export class DynamoDBService {
  private client: DynamoDBClient;
  private tableName: string;

  constructor(config: DynamoDBConfig) {
    this.tableName = config.tableName;
    this.client = new DynamoDBClient({
      region: config.region || process.env.AWS_REGION || 'us-east-1',
    });
  }

  /**
   * Create a new product or datasheet in DynamoDB
   */
  async create(item: Product | Datasheet): Promise<boolean> {
    try {
      const dbItem = this.serializeItem(item);
      await this.client.send(
        new PutItemCommand({
          TableName: this.tableName,
          Item: marshall(dbItem, { removeUndefinedValues: true }),
        })
      );
      return true;
    } catch (error) {
      console.error('Error creating item:', error);
      return false;
    }
  }

  /**
   * Read a product by ID and type
   */
  async read(productId: string, productType: ProductType): Promise<Product | null> {
    try {
      const typeUpper = productType.toUpperCase();
      const pk = `PRODUCT#${typeUpper}`;
      const sk = `PRODUCT#${productId}`;

      const result = await this.client.send(
        new GetItemCommand({
          TableName: this.tableName,
          Key: marshall({ PK: pk, SK: sk }),
        })
      );

      if (!result.Item) {
        return null;
      }

      return this.deserializeProduct(unmarshall(result.Item));
    } catch (error) {
      console.error('Error reading product:', error);
      return null;
    }
  }

  /**
   * Delete a product by ID and type
   */
  async delete(productId: string, productType: ProductType): Promise<boolean> {
    try {
      const typeUpper = productType.toUpperCase();
      const pk = `PRODUCT#${typeUpper}`;
      const sk = `PRODUCT#${productId}`;

      await this.client.send(
        new DeleteItemCommand({
          TableName: this.tableName,
          Key: marshall({ PK: pk, SK: sk }),
        })
      );

      return true;
    } catch (error) {
      console.error('Error deleting product:', error);
      return false;
    }
  }



  /**
   * Delete products by manufacturer
   * Restricted to deleting only Products (not Datasheets)
   */
  async deleteByManufacturer(manufacturer: string): Promise<{ deleted: number; failed: number }> {
    return this.deleteByScan('manufacturer', manufacturer, 'PRODUCT#');
  }

  /**
   * Delete products by product name
   * Restricted to deleting only Products (not Datasheets)
   */
  async deleteByProductName(name: string): Promise<{ deleted: number; failed: number }> {
    return this.deleteByScan('product_name', name, 'PRODUCT#');
  }

  /**
   * Helper to delete items found by scanning a specific attribute
   */
  private async deleteByScan(
    attributeName: string, 
    attributeValue: string,
    pkPrefix?: string
  ): Promise<{ deleted: number; failed: number }> {
    try {
      // CR/LF strip inline on each user-controlled value (CodeQL
      // js/log-injection barrier; see util/log.ts).
      const safeAttr = attributeName.replace(/\r|\n/g, '');
      const safeVal = attributeValue.replace(/\r|\n/g, '');
      const safePrefix = pkPrefix?.replace(/\r|\n/g, '');
      console.log(`[DynamoDB] Scanning for items where ${safeLog(safeAttr)} = ${safeLog(safeVal)}${safePrefix ? ` (PK starts with ${safeLog(safePrefix)})` : ''}`);
      
      const scanResult = await this.client.send(
        new ScanCommand({
          TableName: this.tableName,
          FilterExpression: `#attr = :val`,
          ExpressionAttributeNames: { '#attr': attributeName },
          ExpressionAttributeValues: marshall({ ':val': attributeValue }),
          ProjectionExpression: 'PK, SK',
        })
      );

      let items = scanResult.Items || [];

      // Filter by PK prefix if specified (to avoid deleting Datasheets when targeting Products)
      if (pkPrefix) {
        items = items.filter(item => item.PK?.S?.startsWith(pkPrefix));
      }

      if (items.length === 0) {
        return { deleted: 0, failed: 0 };
      }

      console.log(`[DynamoDB] Found ${items.length} items to delete`);

      // Delete found items
      const deletePromises = items.map(async (item) => {
        try {
          await this.client.send(
            new DeleteItemCommand({
              TableName: this.tableName,
              Key: {
                PK: item.PK,
                SK: item.SK,
              },
            })
          );
          return true;
        } catch (err) {
          console.error(`[DynamoDB] Failed to delete item ${item.SK?.S}:`, err);
          return false;
        }
      });

      const results = await Promise.all(deletePromises);
      const deleted = results.filter((r) => r).length;
      const failed = results.filter((r) => !r).length;

      return { deleted, failed };
    } catch (error) {
      console.error(`Error deleting products by ${attributeName}:`, error);
      throw error;
    }
  }

  /**
   * Delete products by part number
   * Scans for items with matching part_number and deletes them
   * Restricted to deleting only Products
   */
  async deleteByPartNumber(partNumber: string): Promise<{ deleted: number; failed: number }> {
    return this.deleteByScan('part_number', partNumber, 'PRODUCT#');
  }

  /**
   * List products by type with optional filtering
   * Automatically handles DynamoDB pagination to fetch all results
   */
  async list(
    productType: ProductType = 'all',
    limit?: number
  ): Promise<Product[]> {
    try {
      // If 'all', query all valid product types dynamically
      if (productType === 'all') {
        const allTypePromises = VALID_PRODUCT_TYPES.map(type =>
          this.list(type as ProductType, limit)
        );
        const results = await Promise.all(allTypePromises);
        return results.flat();
      }

      const typeUpper = productType.toUpperCase();
      // Handle datasheets specially as they have a different PK prefix
      const pk = productType === 'datasheet' ? `DATASHEET#${typeUpper}` : `PRODUCT#${typeUpper}`;
      const allItems: Product[] = [];
      let lastEvaluatedKey: Record<string, AttributeValue> | undefined = undefined;

      // Paginate through all results
      let pageCount = 0;
      do {
        pageCount++;
        // CR/LF strip inline (productType is from query string) — see util/log.ts.
        const safeType = productType.replace(/\r|\n/g, '');
        console.log(`[DynamoDB] Query page ${pageCount} for ${safeLog(safeType)} (current total: ${allItems.length})`);

        const result: QueryCommandOutput = await this.client.send(
          new QueryCommand({
            TableName: this.tableName,
            KeyConditionExpression: 'PK = :pk',
            ExpressionAttributeValues: marshall({ ':pk': pk }),
            Limit: limit,
            ExclusiveStartKey: lastEvaluatedKey,
          })
        );

        console.log(`[DynamoDB] Page ${pageCount} returned ${result.Items?.length || 0} items, hasMore: ${!!result.LastEvaluatedKey}`);

        if (result.Items && result.Items.length > 0) {
          const items = result.Items.map((item: Record<string, AttributeValue>) =>
            this.deserializeProduct(unmarshall(item))
          );
          allItems.push(...items);
        }

        // Check if there are more results to fetch
        lastEvaluatedKey = result.LastEvaluatedKey;

        // If a limit was specified and we've reached it, stop paginating
        if (limit && allItems.length >= limit) {
          console.log(`[DynamoDB] Reached limit of ${limit}, stopping pagination`);
          break;
        }

      } while (lastEvaluatedKey);

      // CR/LF strip inline (productType is from query string) — see util/log.ts.
      const safeType = productType.replace(/\r|\n/g, '');
      console.log(`[DynamoDB] Query complete for ${safeLog(safeType)}: ${allItems.length} total items from ${pageCount} pages`);

      return allItems;
    } catch (error) {
      console.error('Error listing products:', error);
      return [];
    }
  }

  /**
   * List all products (convenience method)
   */
  async listAll(limit?: number): Promise<Product[]> {
    return this.list('all', limit);
  }

  /**
   * Batch create multiple products
   * DynamoDB has a limit of 25 items per batch
   */
  async batchCreate(products: Product[]): Promise<number> {
    if (products.length === 0) {
      return 0;
    }

    let successCount = 0;
    const batchSize = 25;

    for (let i = 0; i < products.length; i += batchSize) {
      const batch = products.slice(i, i + batchSize);

      try {
        const requests = batch.map((product) => ({
          PutRequest: {
            Item: marshall(this.serializeProduct(product), {
              removeUndefinedValues: true
            }),
          },
        }));

        await this.client.send(
          new BatchWriteItemCommand({
            RequestItems: {
              [this.tableName]: requests,
            },
          })
        );

        successCount += batch.length;
      } catch (error) {
        console.error('Error in batch create:', error);
        // Continue with next batch even if this one fails
      }
    }

    return successCount;
  }

  /**
   * Batch delete multiple products
   */
  async batchDelete(items: { PK: string; SK: string }[]): Promise<number> {
    if (items.length === 0) {
      return 0;
    }

    let deletedCount = 0;
    const batchSize = 25;

    for (let i = 0; i < items.length; i += batchSize) {
      const batch = items.slice(i, i + batchSize);

      try {
        const requests = batch.map((item) => ({
          DeleteRequest: {
            // Strictly marshal only PK and SK
            Key: marshall({ PK: item.PK, SK: item.SK }),
          },
        }));

        await this.client.send(
          new BatchWriteItemCommand({
            RequestItems: {
              [this.tableName]: requests,
            },
          })
        );

        deletedCount += batch.length;
      } catch (error) {
        console.error('Error in batch delete:', error);
        // Continue with next batch even if this one fails
      }
    }

    return deletedCount;
  }

  /**
   * Count rows for a single product_type via Query with Select=COUNT.
   *
   * Pulls only the per-page count back from DynamoDB (no items, no
   * attributes) — ~99% less data transferred than the prior
   * full-list-then-count.length approach.
   */
  private async countByType(productType: ProductType): Promise<number> {
    const typeUpper = productType.toUpperCase();
    const pk = productType === 'datasheet' ? `DATASHEET#${typeUpper}` : `PRODUCT#${typeUpper}`;
    let total = 0;
    let lastEvaluatedKey: Record<string, AttributeValue> | undefined = undefined;
    do {
      const result: QueryCommandOutput = await this.client.send(
        new QueryCommand({
          TableName: this.tableName,
          KeyConditionExpression: 'PK = :pk',
          ExpressionAttributeValues: marshall({ ':pk': pk }),
          Select: 'COUNT',
          ExclusiveStartKey: lastEvaluatedKey,
        })
      );
      total += result.Count ?? 0;
      lastEvaluatedKey = result.LastEvaluatedKey;
    } while (lastEvaluatedKey);
    return total;
  }

  /**
   * Count products by type
   * Returns counts for all valid product types dynamically
   */
  async count(): Promise<Record<string, number> & { total: number }> {
    const countPromises = VALID_PRODUCT_TYPES.map(async type => ({
      type,
      n: await this.countByType(type as ProductType),
    }));
    const results = await Promise.all(countPromises);

    const counts: Record<string, number> & { total: number } = { total: 0 };
    for (const { type, n } of results) {
      counts[type + 's'] = n; // e.g., "motors", "drives", "robot_arms"
      counts.total += n;
    }
    return counts;
  }

  /**
   * Get all unique product categories and their counts
   * Returns ALL valid product types (from config) with counts from database.
   * Shows types with 0 count if they have no products yet.
   */
  async getCategories(): Promise<Array<{ type: string; count: number; display_name: string }>> {
    try {
      const countPromises = VALID_PRODUCT_TYPES.map(async type => ({
        type,
        count: await this.countByType(type as ProductType),
      }));
      const counts = await Promise.all(countPromises);

      const categories = counts
        .map(({ type, count }) => ({
          type,
          count,
          display_name: formatDisplayName(type),
        }))
        .sort((a, b) => a.type.localeCompare(b.type));

      return categories;
    } catch (error) {
      console.error('Error getting categories:', error);
      return [];
    }
  }

  /**
   * Get all unique manufacturers
   */
  async getUniqueManufacturers(): Promise<string[]> {
    try {
      const allProducts = await this.listAll();
      const manufacturers = new Set(
        allProducts
          .map(p => p.manufacturer)
          .filter((f): f is string => !!f)
      );
      return Array.from(manufacturers).sort();
    } catch (error) {
      console.error('Error getting unique manufacturers:', error);
      return [];
    }
  }

  /**
   * Get all unique product names
   */
  async getUniqueNames(): Promise<string[]> {
    try {
      const allProducts = await this.listAll();
      const names = new Set(
        allProducts
          .map(p => p.product_name)
          .filter((n): n is string => !!n)
      );
      return Array.from(names).sort();
    } catch (error) {
      console.error('Error getting unique names:', error);
      return [];
    }
  }

  /**
   * List all Manufacturer records (PK = MANUFACTURER).
   * Manufacturer is a first-class entity separate from Products; it lives in
   * the same single-table design but with a fixed partition key.
   */
  async listManufacturers(): Promise<Manufacturer[]> {
    try {
      const allItems: Manufacturer[] = [];
      let lastEvaluatedKey: Record<string, AttributeValue> | undefined = undefined;
      do {
        const result: QueryCommandOutput = await this.client.send(
          new QueryCommand({
            TableName: this.tableName,
            KeyConditionExpression: 'PK = :pk',
            ExpressionAttributeValues: marshall({ ':pk': 'MANUFACTURER' }),
            ExclusiveStartKey: lastEvaluatedKey,
          })
        );
        for (const item of result.Items || []) {
          allItems.push(unmarshall(item) as Manufacturer);
        }
        lastEvaluatedKey = result.LastEvaluatedKey;
      } while (lastEvaluatedKey);
      return allItems;
    } catch (error) {
      console.error('Error listing manufacturers:', error);
      return [];
    }
  }

  /**
   * Batch-create Manufacturer records. Items must already have `id`; PK/SK
   * are computed here to match the Python model's pattern.
   */
  async batchCreateManufacturers(manufacturers: Manufacturer[]): Promise<number> {
    if (manufacturers.length === 0) return 0;
    let successCount = 0;
    const batchSize = 25;
    for (let i = 0; i < manufacturers.length; i += batchSize) {
      const batch = manufacturers.slice(i, i + batchSize);
      try {
        const requests = batch.map((m) => ({
          PutRequest: {
            Item: marshall(
              {
                ...m,
                PK: 'MANUFACTURER',
                SK: `MANUFACTURER#${m.id}`,
              },
              { removeUndefinedValues: true }
            ),
          },
        }));
        await this.client.send(
          new BatchWriteItemCommand({
            RequestItems: { [this.tableName]: requests },
          })
        );
        successCount += batch.length;
      } catch (error) {
        console.error('Error in batch create (manufacturers):', error);
      }
    }
    return successCount;
  }

  /**
   * Check if a datasheet exists by URL
   */
  async datasheetExists(url: string): Promise<boolean> {
    try {
      const scanResult = await this.client.send(
        new ScanCommand({
          TableName: this.tableName,
          FilterExpression: '#url = :url',
          ExpressionAttributeNames: { '#url': 'url' },
          ExpressionAttributeValues: marshall({ ':url': url }),
          Limit: 1,
          ProjectionExpression: 'PK',
        })
      );
      return (scanResult.Items?.length || 0) > 0;
    } catch (error) {
      console.error('Error checking datasheet existence:', error);
      return false;
    }
  }

  /**
   * Get a datasheet by URL
   */
  async getDatasheetByUrl(url: string): Promise<Datasheet | null> {
    try {
      const scanResult = await this.client.send(
        new ScanCommand({
          TableName: this.tableName,
          FilterExpression: '#url = :url',
          ExpressionAttributeNames: { '#url': 'url' },
          ExpressionAttributeValues: marshall({ ':url': url }),
          Limit: 1,
        })
      );
      
      if (!scanResult.Items || scanResult.Items.length === 0) {
        return null;
      }
      
      return unmarshall(scanResult.Items[0]) as Datasheet;
    } catch (error) {
      console.error('Error getting datasheet by URL:', error);
      return null;
    }
  }

  /**
   * Check if any PRODUCTS exist for a given datasheet URL
   *
   * Scan's `Limit` caps items examined per page, not items returned post-
   * filter; with a non-indexed `datasheet_url` filter the matching row
   * may sit on page 2+ and a single Scan call returns false even when the
   * URL is in use. Paginate until a match is found or LastEvaluatedKey
   * is exhausted; exit early on the first hit.
   */
  async hasProductsForDatasheetUrl(url: string): Promise<boolean> {
    try {
      let lastEvaluatedKey: Record<string, AttributeValue> | undefined = undefined;
      do {
        const result: ScanCommandOutput = await this.client.send(
          new ScanCommand({
            TableName: this.tableName,
            FilterExpression: 'datasheet_url = :url',
            ExpressionAttributeValues: marshall({ ':url': url }),
            ProjectionExpression: 'PK',
            ExclusiveStartKey: lastEvaluatedKey,
          })
        );
        if (result.Items?.length) return true;
        lastEvaluatedKey = result.LastEvaluatedKey;
      } while (lastEvaluatedKey);
      return false;
    } catch (error) {
      console.error('Error checking products for datasheet URL:', error);
      return false;
    }
  }

  /**
   * List all datasheets
   *
   * Datasheets sit across multiple PK partitions (DATASHEET#GEARHEAD,
   * DATASHEET#MOTOR, ...), so we Scan with a begins_with filter and
   * paginate until LastEvaluatedKey is empty. Without the loop, a single
   * Scan caps at 1MB of pre-filter scanned data — on a table with ~2K
   * product rows that returned only the first ~6 datasheet hits.
   */
  async listDatasheets(): Promise<Datasheet[]> {
    try {
      const items: Datasheet[] = [];
      let lastEvaluatedKey: Record<string, AttributeValue> | undefined = undefined;
      let pageCount = 0;

      do {
        pageCount++;
        const result: ScanCommandOutput = await this.client.send(
          new ScanCommand({
            TableName: this.tableName,
            FilterExpression: 'begins_with(PK, :pk)',
            ExpressionAttributeValues: marshall({ ':pk': 'DATASHEET#' }),
            ExclusiveStartKey: lastEvaluatedKey,
          })
        );
        if (result.Items?.length) {
          items.push(...result.Items.map(item => unmarshall(item) as Datasheet));
        }
        lastEvaluatedKey = result.LastEvaluatedKey;
      } while (lastEvaluatedKey);

      console.log(`[DynamoDB] listDatasheets: ${items.length} datasheets across ${pageCount} scan page(s)`);
      return items;
    } catch (error) {
      console.error('Error listing datasheets:', error);
      return [];
    }
  }

  /**
   * Delete a datasheet
   */
  async deleteDatasheet(id: string, productType: string): Promise<boolean> {
    try {
      const pk = `DATASHEET#${productType.toUpperCase()}`;
      const sk = `DATASHEET#${id}`;

      await this.client.send(
        new DeleteItemCommand({
          TableName: this.tableName,
          Key: marshall({ PK: pk, SK: sk }),
        })
      );
      return true;
    } catch (error) {
      console.error('Error deleting datasheet:', error);
      return false;
    }
  }

  /**
   * Update a product by ID and type
   */
  async updateProduct(id: string, productType: string, updates: Partial<Product>): Promise<boolean> {
    try {
      const pk = `PRODUCT#${productType.toUpperCase()}`;
      const sk = `PRODUCT#${id}`;

      const result = await this.client.send(
        new GetItemCommand({
          TableName: this.tableName,
          Key: marshall({ PK: pk, SK: sk }),
        })
      );

      if (!result.Item) {
        return false;
      }

      const existingItem = unmarshall(result.Item) as Product;

      const updatedItem = {
        ...existingItem,
        ...updates,
        product_id: id,
        product_type: existingItem.product_type,
        PK: pk,
        SK: sk,
      };

      await this.client.send(
        new PutItemCommand({
          TableName: this.tableName,
          Item: marshall(updatedItem, { removeUndefinedValues: true }),
        })
      );

      return true;
    } catch (error) {
      console.error('Error updating product:', error);
      return false;
    }
  }

  /**
   * Update a datasheet
   */
  async updateDatasheet(id: string, productType: string, updates: Partial<Datasheet>): Promise<boolean> {
    try {
      const pk = `DATASHEET#${productType.toUpperCase()}`;
      const sk = `DATASHEET#${id}`;

      // Fetch existing item first to ensure it exists and preserve other fields
      const result = await this.client.send(
        new GetItemCommand({
          TableName: this.tableName,
          Key: marshall({ PK: pk, SK: sk }),
        })
      );

      if (!result.Item) {
        return false;
      }

      const existingItem = unmarshall(result.Item) as Datasheet;
      
      // Merge updates
      const updatedItem: Datasheet = {
        ...existingItem,
        ...updates,
        // Ensure keys don't change
        datasheet_id: id,
        product_type: existingItem.product_type,
        PK: pk,
        SK: sk
      };

      await this.client.send(
        new PutItemCommand({
          TableName: this.tableName,
          Item: marshall(updatedItem, { removeUndefinedValues: true }),
        })
      );

      return true;
    } catch (error) {
      console.error('Error updating datasheet:', error);
      return false;
    }
  }

  /**
   * Serialize item (Product or Datasheet) for DynamoDB storage
   */
  private serializeItem(item: Product | Datasheet): any {
    // Check if it's a Datasheet (has url property)
    if ('url' in item && !('product_id' in item)) {
      const ds = item as Datasheet;
      const typeUpper = ds.product_type.toUpperCase();
      return {
        ...ds,
        PK: `DATASHEET#${typeUpper}`,
        SK: `DATASHEET#${ds.datasheet_id}`,
      };
    }

    // It's a Product (Motor or Drive)
    const product = item as Motor | Drive;
    const typeUpper = product.product_type.toUpperCase();
    return {
      ...product,
      PK: `PRODUCT#${typeUpper}`,
      SK: `PRODUCT#${product.product_id}`,
    };
  }

  /**
   * Serialize product for DynamoDB storage
   * @deprecated Use serializeItem instead
   */
  private serializeProduct(product: Product): any {
    return this.serializeItem(product);
  }

  /**
   * Deserialize product from DynamoDB.
   *
   * ValueUnit / MinMaxUnit fields are persisted as nested objects
   * (`{value, unit}` / `{min, max, unit}`), the same shape the Python
   * Pydantic models emit and the frontend consumes — no compact-string
   * parsing required.
   */
  private deserializeProduct(item: any): Product {
    // Handle datasheet mapping for frontend compatibility
    if (item.product_type === 'datasheet' && item.datasheet_id && !item.product_id) {
      return {
        ...item,
        product_id: item.datasheet_id,
      } as Product;
    }
    return item as Product;
  }
}
