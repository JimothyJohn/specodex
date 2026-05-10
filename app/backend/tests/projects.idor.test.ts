/**
 * IDOR (Insecure Direct Object Reference) tests for /api/projects/*.
 *
 * The auth.routes test exercises the public unauthenticated paths.
 * The projects.test exercises the happy path for a single user. This
 * file specifically asserts **cross-tenant isolation**: given a JWT
 * for user B, the route MUST NOT return, modify, or delete data
 * belonging to user A — even when user B knows or guesses A's
 * resource ID.
 *
 * The DB layer scopes every read/write by `(owner_sub, project_id)`.
 * The route layer reads `req.user.sub` from the validated JWT and
 * passes it to the DB. This file verifies the END-TO-END contract:
 * if a future refactor accidentally drops the sub scoping (e.g. a
 * developer pulls the `id` from the URL but forgets the sub from
 * the JWT, or relies on the JWT's email instead of the sub), these
 * tests fail.
 */

import request from 'supertest';

const mockVerify = jest.fn();
jest.mock('aws-jwt-verify', () => ({
  CognitoJwtVerifier: { create: jest.fn(() => ({ verify: mockVerify })) },
}));

// In-memory project store keyed by (owner_sub, project_id) — simulates
// the DB layer's sub scoping. If the route forgets to pass the JWT sub,
// the get/rename/delete/etc. methods are called with the WRONG sub and
// return undefined — driving the route to 404.
type Project = {
  id: string;
  name: string;
  owner_sub: string;
  product_refs: unknown[];
  created_at: string;
  updated_at: string;
};

const store: Map<string, Map<string, Project>> = new Map();

function _put(sub: string, project: Project) {
  if (!store.has(sub)) store.set(sub, new Map());
  store.get(sub)!.set(project.id, project);
}

function _get(sub: string, id: string): Project | undefined {
  return store.get(sub)?.get(id);
}

function _del(sub: string, id: string): boolean {
  const m = store.get(sub);
  if (!m || !m.has(id)) return false;
  m.delete(id);
  return true;
}

const mockList = jest.fn(async (sub: string) =>
  Array.from(store.get(sub)?.values() ?? []),
);
const mockGet = jest.fn(async (sub: string, id: string) => _get(sub, id) ?? null);
const mockCreate = jest.fn(async (sub: string, project: Project) => {
  _put(sub, project);
});
const mockRename = jest.fn(async (sub: string, id: string, name: string) => {
  const p = _get(sub, id);
  if (!p) return null;
  p.name = name;
  return p;
});
const mockDelete = jest.fn(async (sub: string, id: string) => _del(sub, id));
const mockAddProduct = jest.fn(
  async (sub: string, id: string, _ref: unknown) => {
    const p = _get(sub, id);
    return p ?? null;
  },
);
const mockRemoveProduct = jest.fn(
  async (sub: string, id: string, _ref: unknown) => {
    const p = _get(sub, id);
    return p ?? null;
  },
);

jest.mock('../src/db/projects', () => ({
  ProjectsService: jest.fn().mockImplementation(() => ({
    list: mockList,
    get: mockGet,
    create: mockCreate,
    rename: mockRename,
    delete: mockDelete,
    addProduct: mockAddProduct,
    removeProduct: mockRemoveProduct,
  })),
}));

jest.mock('../src/db/dynamodb');

import config from '../src/config';
import app from '../src/index';
import { _resetVerifierForTests } from '../src/middleware/auth';

const ALICE = 'user-sub-alice';
const BOB = 'user-sub-bob';
const TOKEN = 'Bearer test-token';

function authedUser(sub: string) {
  return { sub, email: `${sub}@example.com`, 'cognito:groups': [] };
}

beforeEach(() => {
  jest.clearAllMocks();
  store.clear();
  _resetVerifierForTests();
  config.cognito.userPoolId = 'us-east-1_TEST';
  config.cognito.userPoolClientId = 'test-client-id';
});

/** Have *sub* hit the API once with a fresh verifier mock. */
function asUser(sub: string) {
  mockVerify.mockResolvedValueOnce(authedUser(sub));
  return request(app);
}

/** Seed Alice's data store with one project, returning its ID. */
function seedAliceProject(id = 'alice-private-project') {
  const now = '2026-05-09T00:00:00Z';
  _put(ALICE, {
    id,
    name: 'Alice Private',
    owner_sub: ALICE,
    product_refs: [],
    created_at: now,
    updated_at: now,
  });
  return id;
}

// --------------------------------------------------------------------
// GET /api/projects — list isolation
// --------------------------------------------------------------------

describe('GET /api/projects (list isolation)', () => {
  it('returns only the caller’s projects, not other users’', async () => {
    seedAliceProject('alice-1');
    _put(BOB, {
      id: 'bob-1',
      name: 'Bob Private',
      owner_sub: BOB,
      product_refs: [],
      created_at: '2026-05-09T00:00:00Z',
      updated_at: '2026-05-09T00:00:00Z',
    });

    const res = await asUser(BOB).get('/api/projects').set('Authorization', TOKEN);

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.data).toHaveLength(1);
    expect(res.body.data[0].id).toBe('bob-1');
    // Critical: the route asks the DB for BOB's projects, never ALICE's.
    expect(mockList).toHaveBeenCalledWith(BOB);
    expect(mockList).not.toHaveBeenCalledWith(ALICE);
  });
});

// --------------------------------------------------------------------
// GET /api/projects/:id — read isolation
// --------------------------------------------------------------------

describe('GET /api/projects/:id (read isolation)', () => {
  it('returns 404 when Bob requests Alice’s project ID', async () => {
    const aliceId = seedAliceProject();

    const res = await asUser(BOB)
      .get(`/api/projects/${aliceId}`)
      .set('Authorization', TOKEN);

    expect(res.status).toBe(404);
    // Body must not contain Alice's name even by accident.
    expect(JSON.stringify(res.body)).not.toContain('Alice Private');
    // The DB lookup must be scoped to Bob, never Alice.
    expect(mockGet).toHaveBeenCalledWith(BOB, aliceId);
    expect(mockGet).not.toHaveBeenCalledWith(ALICE, aliceId);
  });

  it('returns 200 when Alice requests her own project', async () => {
    const id = seedAliceProject();

    const res = await asUser(ALICE)
      .get(`/api/projects/${id}`)
      .set('Authorization', TOKEN);

    expect(res.status).toBe(200);
    expect(res.body.data.id).toBe(id);
    expect(mockGet).toHaveBeenCalledWith(ALICE, id);
  });
});

// --------------------------------------------------------------------
// PATCH /api/projects/:id — write isolation (rename)
// --------------------------------------------------------------------

describe('PATCH /api/projects/:id (write isolation)', () => {
  it('returns 404 when Bob tries to rename Alice’s project', async () => {
    const aliceId = seedAliceProject();
    const beforeName = _get(ALICE, aliceId)!.name;

    const res = await asUser(BOB)
      .patch(`/api/projects/${aliceId}`)
      .set('Authorization', TOKEN)
      .send({ name: 'pwned-by-bob' });

    expect(res.status).toBe(404);
    // Alice's project name must be unchanged in the store.
    expect(_get(ALICE, aliceId)!.name).toBe(beforeName);
    expect(mockRename).toHaveBeenCalledWith(BOB, aliceId, 'pwned-by-bob');
    expect(mockRename).not.toHaveBeenCalledWith(ALICE, aliceId, expect.anything());
  });
});

// --------------------------------------------------------------------
// DELETE /api/projects/:id — write isolation (delete)
// --------------------------------------------------------------------

describe('DELETE /api/projects/:id (write isolation)', () => {
  it('returns 404 when Bob tries to delete Alice’s project (and Alice keeps it)', async () => {
    const aliceId = seedAliceProject();

    const res = await asUser(BOB)
      .delete(`/api/projects/${aliceId}`)
      .set('Authorization', TOKEN);

    expect(res.status).toBe(404);
    // Alice's project still exists.
    expect(_get(ALICE, aliceId)).toBeDefined();
    expect(mockDelete).toHaveBeenCalledWith(BOB, aliceId);
    expect(mockDelete).not.toHaveBeenCalledWith(ALICE, aliceId);
  });

  it('returns 200 when Alice deletes her own project', async () => {
    const id = seedAliceProject();

    const res = await asUser(ALICE)
      .delete(`/api/projects/${id}`)
      .set('Authorization', TOKEN);

    expect(res.status).toBe(200);
    expect(_get(ALICE, id)).toBeUndefined();
  });
});

// --------------------------------------------------------------------
// POST /api/projects/:id/products — write isolation (add product)
// --------------------------------------------------------------------

describe('POST /api/projects/:id/products (write isolation)', () => {
  it('returns 404 when Bob tries to add a product to Alice’s project', async () => {
    const aliceId = seedAliceProject();

    const res = await asUser(BOB)
      .post(`/api/projects/${aliceId}/products`)
      .set('Authorization', TOKEN)
      .send({ product_type: 'motor', product_id: 'P-123' });

    expect(res.status).toBe(404);
    expect(mockAddProduct).toHaveBeenCalledWith(BOB, aliceId, expect.anything());
    expect(mockAddProduct).not.toHaveBeenCalledWith(
      ALICE,
      aliceId,
      expect.anything(),
    );
  });
});

// --------------------------------------------------------------------
// DELETE /api/projects/:id/products/:type/:pid — write isolation (remove product)
// --------------------------------------------------------------------

describe('DELETE /api/projects/:id/products/:type/:pid (write isolation)', () => {
  it('returns 404 when Bob tries to remove a product from Alice’s project', async () => {
    const aliceId = seedAliceProject();

    const res = await asUser(BOB)
      .delete(`/api/projects/${aliceId}/products/motor/P-123`)
      .set('Authorization', TOKEN);

    expect(res.status).toBe(404);
    expect(mockRemoveProduct).toHaveBeenCalledWith(
      BOB,
      aliceId,
      expect.anything(),
    );
    expect(mockRemoveProduct).not.toHaveBeenCalledWith(
      ALICE,
      aliceId,
      expect.anything(),
    );
  });
});

// --------------------------------------------------------------------
// Cross-cutting: enumeration / sequential ID guessing
// --------------------------------------------------------------------

describe('ID enumeration (sequential guessing)', () => {
  it('does not leak whether a foreign project ID exists vs not', async () => {
    seedAliceProject('alice-real-id');

    const known = await asUser(BOB)
      .get('/api/projects/alice-real-id')
      .set('Authorization', TOKEN);
    const random = await asUser(BOB)
      .get('/api/projects/never-existed')
      .set('Authorization', TOKEN);

    // Both must look identical to Bob — same status, same body shape.
    expect(known.status).toBe(404);
    expect(random.status).toBe(404);
    expect(known.body).toEqual(random.body);
  });
});

// --------------------------------------------------------------------
// Cross-cutting: forged sub via custom header / body
// --------------------------------------------------------------------

describe('forged-identity attempts', () => {
  it('ignores X-User-Id header (not part of the auth contract)', async () => {
    const aliceId = seedAliceProject();

    const res = await asUser(BOB)
      .get(`/api/projects/${aliceId}`)
      .set('Authorization', TOKEN)
      .set('X-User-Id', ALICE);

    expect(res.status).toBe(404);
    expect(mockGet).toHaveBeenCalledWith(BOB, aliceId);
  });

  it('ignores body-supplied owner_sub on POST', async () => {
    const res = await asUser(BOB)
      .post('/api/projects')
      .set('Authorization', TOKEN)
      .send({ name: 'forged', owner_sub: ALICE });

    // Project was created — but always with Bob's sub, never Alice's.
    expect([200, 201]).toContain(res.status);
    expect(mockCreate).toHaveBeenCalledWith(BOB, expect.anything());
    expect(mockCreate).not.toHaveBeenCalledWith(ALICE, expect.anything());

    // And the stored owner_sub really is Bob's.
    const created = Array.from(store.get(BOB)?.values() ?? [])[0];
    expect(created.owner_sub).toBe(BOB);
  });
});
