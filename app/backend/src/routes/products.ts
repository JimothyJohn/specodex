/**
 * API routes for products
 * Mirrors functionality from query.py and pusher.py
 */

import { Router, Request, Response } from 'express';
import { DynamoDBService } from '../db/dynamodb';
import { Product, ProductType } from '../types/models';
import { v4 as uuidv4 } from 'uuid';
import config from '../config';

const router = Router();
const db = new DynamoDBService({ tableName: config.dynamodb.tableName });

/**
 * GET /api/products/categories
 * Get all unique product categories with counts
 */
router.get('/categories', async (_req: Request, res: Response) => {
  try {
    const categories = await db.getCategories();
    res.json({
      success: true,
      data: categories,
    });
  } catch (error) {
    console.error('Error getting categories:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get categories',
    });
  }
});

/**
 * GET /api/products/manufacturers
 * Get all unique manufacturers
 */
router.get('/manufacturers', async (_req: Request, res: Response) => {
  try {
    const manufacturers = await db.getUniqueManufacturers();
    res.json({
      success: true,
      data: manufacturers,
    });
  } catch (error) {
    console.error('Error getting manufacturers:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get manufacturers',
    });
  }
});

/**
 * GET /api/products/names
 * Get all unique product names
 */
router.get('/names', async (_req: Request, res: Response) => {
  try {
    const names = await db.getUniqueNames();
    res.json({
      success: true,
      data: names,
    });
  } catch (error) {
    console.error('Error getting names:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get names',
    });
  }
});

/**
 * GET /api/products/summary
 * Get summary statistics about products in the database
 */
router.get('/summary', async (_req: Request, res: Response) => {
  try {
    const counts = await db.count();
    res.json({
      success: true,
      data: counts,
    });
  } catch (error) {
    console.error('Error getting summary:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get summary',
    });
  }
});

/**
 * GET /api/products
 * List products with optional filtering
 * Query params: type (any product type or 'all'), limit (number)
 */
router.get('/', async (req: Request, res: Response): Promise<void> => {
  try {
    const type = (req.query.type as ProductType) || 'all';
    const limit = req.query.limit ? parseInt(req.query.limit as string, 10) : undefined;

    // Accept any product type - no validation needed
    // The database will return empty array if type doesn't exist

    const products = await db.list(type, limit);

    res.json({
      success: true,
      data: products,
      count: products.length,
    });
  } catch (error) {
    console.error('Error listing products:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to list products',
    });
  }
});

/**
 * GET /api/products/:id
 * Get a specific product by ID
 * Query params: type (any valid product type) - required
 */
router.get('/:id', async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;
    const type = req.query.type as ProductType;

    if (!type) {
      res.status(400).json({
        success: false,
        error: 'type query parameter is required',
      });
      return;
    }

    const product = await db.read(id, type);

    if (!product) {
      res.status(404).json({
        success: false,
        error: 'Product not found',
      });
      return;
    }

    res.json({
      success: true,
      data: product,
    });
  } catch (error) {
    console.error('Error getting product:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get product',
    });
  }
});

/**
 * POST /api/products
 * Create a new product or batch create multiple products
 * Body: Product | Product[]
 */
router.post('/', async (req: Request, res: Response): Promise<void> => {
  try {
    const body = req.body;

    // Reject primitives / null / malformed bodies up front — `[body]` on a
    // primitive walks into `'url' in product`, which throws TypeError and
    // bubbles into a 500. Keep the shape-check tight: object or object[].
    const isPlainObject = (v: unknown): v is Record<string, unknown> =>
      typeof v === 'object' && v !== null && !Array.isArray(v);
    const shapeOk =
      isPlainObject(body) ||
      (Array.isArray(body) && body.every(isPlainObject));
    if (!shapeOk) {
      res.status(400).json({
        success: false,
        error: 'Body must be a product object or an array of product objects',
      });
      return;
    }

    // Handle both single product and array of products
    const products: Product[] = (Array.isArray(body) ? body : [body]) as unknown as Product[];

    // Validate products have required fields
    for (const product of products) {
      if (!product.product_type) {
        res.status(400).json({
          success: false,
          error: 'Each product must have a product_type field',
        });
        return;
      }

      if (!(product as any).manufacturer) {
        res.status(400).json({
          success: false,
          error: 'Each product must have a manufacturer field',
        });
        return;
      }

      // Check if it's a datasheet (has url) or a regular product
      if ('url' in product) {
        // It's a datasheet
        if (!(product as any).datasheet_id) {
          (product as any).datasheet_id = uuidv4();
        }
      } else {
        // It's a regular product
        if (!(product as any).product_id) {
          (product as any).product_id = uuidv4();
        }
      }
    }

    // Use batch create if multiple products
    let successCount: number;
    if (products.length > 1) {
      successCount = await db.batchCreate(products);
    } else {
      const success = await db.create(products[0]);
      successCount = success ? 1 : 0;
    }

    const failureCount = products.length - successCount;

    res.status(201).json({
      success: successCount > 0,
      data: {
        items_received: products.length,
        items_created: successCount,
        items_failed: failureCount,
      },
    });
  } catch (error) {
    console.error('Error creating product(s):', error);
    res.status(500).json({
      success: false,
      error: 'Failed to create product(s)',
    });
  }
});

/**
 * PUT /api/products/:id
 * Update a product by ID
 * Query params: type (any valid product type) - required
 */
router.put('/:id', async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;
    const type = req.query.type as ProductType;

    if (!type) {
      res.status(400).json({ success: false, error: 'type query parameter is required' });
      return;
    }

    const success = await db.updateProduct(id, type, req.body);

    if (!success) {
      res.status(404).json({ success: false, error: 'Product not found or failed to update' });
      return;
    }

    res.json({ success: true, message: 'Product updated successfully' });
  } catch (error) {
    console.error('Error updating product:', error);
    res.status(500).json({ success: false, error: 'Failed to update product' });
  }
});

/**
 * DELETE /api/products/:id
 * Delete a product by ID
 * Query params: type (any valid product type) - required
 */
router.delete('/:id', async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;
    const type = req.query.type as ProductType;

    if (!type) {
      res.status(400).json({
        success: false,
        error: 'type query parameter is required',
      });
      return;
    }

    // 1. Get product before deletion to find datasheet_url
    const product = await db.read(id, type);
    
    // 2. Delete product
    const success = await db.delete(id, type);

    if (!success) {
      res.status(404).json({
        success: false,
        error: 'Product not found or failed to delete',
      });
      return;
    }

    // 3. Update Datasheet Status if it was the last product
    if (product && 'datasheet_url' in product) {
        // Safe access after check, or use casting if TS is stubborn about Union
        const dsUrl = (product as any).datasheet_url;
        
        if (dsUrl) {
            const remainingProducts = await db.hasProductsForDatasheetUrl(dsUrl);
            
            if (!remainingProducts) {
                // dsUrl embeds the user-supplied filename via s3Key — strip
                // CR/LF inline before logging (CodeQL js/log-injection barrier;
                // see util/log.ts).
                const safeDsUrl = dsUrl.replace(/\r|\n/g, '');
                console.log(`[Delete] No products remaining for datasheet: ${safeDsUrl}. Resetting status.`);
                const datasheet = await db.getDatasheetByUrl(dsUrl);
                
                if (datasheet && datasheet.datasheet_id) {
                    // Update datasheet to remove last_scraped
                    await db.updateDatasheet(datasheet.datasheet_id, datasheet.product_type, {
                        last_scraped: undefined 
                    });
                    console.log(`[Delete] Datasheet ${datasheet.datasheet_id} marked as not scraped.`);
                }
            }
        }
    }

    res.json({
      success: true,
      message: 'Product deleted successfully',
    });
  } catch (error) {
    console.error('Error deleting product:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to delete product',
    });
  }
});

/**
 * DELETE /api/products/part-number/:partNumber
 * Delete all products with a specific part number
 */
router.delete('/part-number/:partNumber', async (req: Request, res: Response): Promise<void> => {
  try {
    const { partNumber } = req.params;

    if (!partNumber) {
      res.status(400).json({
        success: false,
        error: 'Part number is required',
      });
      return;
    }

    const result = await db.deleteByPartNumber(partNumber);

    if (result.deleted === 0 && result.failed === 0) {
      res.status(404).json({
        success: false,
        error: 'No products found with this part number',
      });
      return;
    }

    res.json({
      success: true,
      data: result,
      message: `Deleted ${result.deleted} products (Failed: ${result.failed})`,
    });
  } catch (error) {
    console.error('Error deleting products by part number:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to delete products',
    });
  }
});

/**
 * DELETE /api/products/manufacturer/:manufacturer
 * Delete all products with a specific manufacturer
 */
router.delete('/manufacturer/:manufacturer', async (req: Request, res: Response): Promise<void> => {
  try {
    const { manufacturer } = req.params;
    const result = await db.deleteByManufacturer(manufacturer);
    
    if (result.deleted === 0 && result.failed === 0) {
      res.status(404).json({ success: false, error: 'No products found with this manufacturer' });
      return;
    }

    res.json({
      success: true,
      data: result,
      message: `Deleted ${result.deleted} products (Failed: ${result.failed})`,
    });
  } catch (error) {
    console.error('Error deleting products by manufacturer:', error);
    res.status(500).json({ success: false, error: 'Failed to delete products' });
  }
});

/**
 * DELETE /api/products/name/:name
 * Delete all products with a specific name
 */
router.delete('/name/:name', async (req: Request, res: Response): Promise<void> => {
  try {
    const { name } = req.params;
    const result = await db.deleteByProductName(name);
    
    if (result.deleted === 0 && result.failed === 0) {
      res.status(404).json({ success: false, error: 'No products found with this name' });
      return;
    }

    res.json({
      success: true,
      data: result,
      message: `Deleted ${result.deleted} products (Failed: ${result.failed})`,
    });
  } catch (error) {
    console.error('Error deleting products by name:', error);
    res.status(500).json({ success: false, error: 'Failed to delete products' });
  }
});

/**
 * POST /api/products/deduplicate
 * Find and delete duplicate products
 * Body: { confirm: boolean }
 */
// POST /api/products/deduplicate
router.post('/deduplicate', async (req: Request, res: Response): Promise<void> => {
  try {
    const { confirm } = req.body;
    
    // 1. Fetch all products
    const allProducts = await db.listAll();
    console.log(`[Deduplicate] Scanned ${allProducts.length} items`);

    // 2. Group items by part_number + product_name + manufacturer
    const groups = new Map<string, Product[]>();

    for (const item of allProducts) {
      const p = item as any; // Cast to access potential fields across Union types
      const partNumber = (p.part_number || '').trim();
      const productName = (p.product_name || '').trim();
      const manufacturer = (p.manufacturer || '').trim();

      // Create unique key
      const key = `${partNumber}|${productName}|${manufacturer}`;
      
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key)!.push(item);
    }

    // 3. Find duplicates
    const duplicatesFound: Product[] = [];
    const itemsToDelete: { PK: string; SK: string, product_id?: string }[] = [];
    const duplicatePartNumbers = new Set<string>();
    let duplicateGroupsCount = 0;

    for (const items of groups.values()) {
      if (items.length > 1) {
        duplicateGroupsCount++;
        
        // Sort to be deterministic (by product_id)
        items.sort((a, b) => {
          const idA = (a as any).product_id || (a as any).datasheet_id || '';
          const idB = (b as any).product_id || (b as any).datasheet_id || '';
          return idA.localeCompare(idB);
        });
        
        // Keep the first one, delete the rest
        const toDelete = items.slice(1);
        duplicatesFound.push(...toDelete);
        
        // Collect part numbers for display
        const partNum = ((items[0] as any).part_number || '').trim();
        if (partNum) {
            duplicatePartNumbers.add(partNum);
        }
        
        for (const item of toDelete) {
          const id = (item as any).product_id || (item as any).datasheet_id;
          if (item.PK && item.SK) {
             itemsToDelete.push({ PK: item.PK!, SK: item.SK!, product_id: id });
          }
        }
      }
    }

    const result = {
      found: duplicatesFound.length,
      deleted: 0,
      unique_part_numbers: groups.size,
      duplicate_groups: duplicateGroupsCount,
      duplicate_part_numbers: Array.from(duplicatePartNumbers).sort(),
      dry_run: !confirm
    };

    if (!confirm) {
      console.log(`[Deduplicate] DRY RUN: Found ${duplicatesFound.length} duplicates to delete`);
      res.json({ success: true, data: result });
      return;
    }

    // 4. Delete items
    if (itemsToDelete.length > 0) {
      console.log(`[Deduplicate] Deleting ${itemsToDelete.length} items...`);
      const deletedCount = await db.batchDelete(itemsToDelete);
      result.deleted = deletedCount;
      console.log(`[Deduplicate] Successfully deleted ${deletedCount} items`);
    }

    res.json({ success: true, data: result });

  } catch (error) {
    console.error('Error in deduplicate endpoint:', error);
    res.status(500).json({
      success: false,
      error: 'Internal server error while deduplicating'
    });
  }
});

export default router;
