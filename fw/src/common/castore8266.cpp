// CA certificates for BearSSL (ESP8266), from flash
//
// As the CertStore of the ESP8266 core, but the certificates are in flash (ca_store.h, made from the Debian package
// ca-certificates by tools/gen_ca_bundle.py at build time), not in a file system. BearSSL asks for the trust anchor
// with the SHA-256 of the issuer name. The store has the SHA-256 of the subject name of each certificate. On a match,
// the certificate is copied to RAM and decoded (X509List) until BearSSL frees it.

#if defined(ESP8266)

#include <Arduino.h>

#include <new>

#include "castore8266.hpp"
#include <ca_store.h>

/**
 * @brief Copy certificate from flash
 *
 * @param[in]  ix   Certificate index
 * @param[out] len  Length
 *
 * @returns the certificate (malloc), nullptr if no memory
 */
static uint8_t* copyCert(const uint32_t ix, size_t& len)
{
    const uint32_t start = pgm_read_dword(&CA_STORE_POS[ix]);
    len = pgm_read_dword(&CA_STORE_POS[ix + 1]) - start;
    uint8_t* der = (uint8_t*)malloc(len);
    if (der != nullptr) {
        memcpy_P(der, &CA_STORE_DER[start], len);
    }
    return der;
}

void CaStore::installCertStore(br_x509_minimal_context* ctx)
{
    br_x509_minimal_set_dynamic(ctx, (void*)this, findTa, freeTa);
}

const br_x509_trust_anchor* CaStore::findTa(void* ctx, void* hashedDn, size_t len)
{
    CaStore* store = static_cast<CaStore*>(ctx);
    if ((store == nullptr) || (len != 32)) {
        return nullptr;
    }
    for (uint32_t ix = 0; ix < CA_STORE_NUM; ix++) {
        uint8_t hash[32];
        memcpy_P(hash, CA_STORE_HASH[ix], sizeof(hash));
        if (memcmp(hash, hashedDn, sizeof(hash)) != 0) {
            continue;
        }
        size_t derLen = 0;
        uint8_t* der = copyCert(ix, derLen);
        if (der == nullptr) {
            return nullptr;
        }
        delete store->x509;
        store->x509 = new (std::nothrow) BearSSL::X509List(der, derLen);
        free(der);
        if (store->x509 == nullptr) {
            return nullptr;
        }
        // BearSSL compares the hashed name (as CertStore does)
        br_x509_trust_anchor* ta = (br_x509_trust_anchor*)store->x509->getTrustAnchors();
        memcpy(ta->dn.data, hash, sizeof(hash));
        ta->dn.len = sizeof(hash);
        return ta;
    }
    return nullptr;
}

void CaStore::freeTa(void* ctx, const br_x509_trust_anchor* ta)
{
    (void)ta;
    CaStore* store = static_cast<CaStore*>(ctx);
    delete store->x509;
    store->x509 = nullptr;
}

extern "C" {
/**
 * @brief X.509 decoder callback: hash the subject name
 */
static void hashName(void* ctx, const void* buf, size_t len)
{
    br_sha256_update((br_sha256_context*)ctx, buf, len);
}
}

bool CaStore::check()
{
    bool ok = true;
    for (const uint32_t ix : { (uint32_t)0, (CA_STORE_NUM - 1) }) {
        size_t len = 0;
        uint8_t* der = copyCert(ix, len);
        br_x509_decoder_context* dec = new (std::nothrow) br_x509_decoder_context;
        br_sha256_context* sha = new (std::nothrow) br_sha256_context;
        if ((der != nullptr) && (dec != nullptr) && (sha != nullptr)) {
            uint8_t hash[32];
            uint8_t want[32];
            br_sha256_init(sha);
            br_x509_decoder_init(dec, hashName, sha, nullptr, nullptr);
            br_x509_decoder_push(dec, der, len);
            br_sha256_out(sha, hash);
            memcpy_P(want, CA_STORE_HASH[ix], sizeof(want));
            ok = (ok && (memcmp(hash, want, sizeof(hash)) == 0));
        } else {
            ok = false;
        }
        free(der);
        delete dec;
        delete sha;
    }
    Serial.printf("CA store: %u certificates, check %s\r\n", (unsigned)CA_STORE_NUM, (ok ? "OK" : "FAILED"));
    return ok;
}

#endif // ESP8266
