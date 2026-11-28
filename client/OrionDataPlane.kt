package com.orion.store.data

/**
 * Reference decoder for the Orion data plane, schema 3.
 *
 * Copy this file into Orion-Store at
 *   app/src/main/java/com/orion/store/data/OrionDataPlane.kt
 *
 * It is deliberately dependency-free: no kotlinx-serialization, no androidx.
 * The byte layout it reads is specified in docs/DATA_CONTRACT.md and is
 * implemented, exercised and round-trip tested in scripts/orion_data.py
 * (`encode_catalog` / `decode_catalog` / `encode_versions` are the authority).
 *
 * Verify after wiring it up:
 *   index.json has "apps": N, and decodeCatalog over all parts must yield N
 *   distinct ids. If it does not, stop: the format has drifted.
 *
 * Kotlin note: Long arithmetic wraps on overflow, which is exactly what FNV-1a
 * needs. Do not "fix" it with Math.multiplyExact or checked arithmetic.
 */
object OrionDataPlane {

    const val SCHEMA = 3

    /** Column order in a tier-0 record. Do not reorder without bumping SCHEMA. */
    val COLUMNS = listOf(
        "id", "name", "icon", "category", "author",
        "packageName", "repo", "version", "size", "platform",
        "downloadUrl", "releaseKeyword",
    )

    /** Index of the platform byte, and 255 means "unknown". */
    val PLATFORMS = listOf("Android", "Windows", "Linux", "iOS", "Web")

    /** One row of tier 0 — everything needed to draw a card and start an install. */
    data class CatalogApp(
        val id: String,
        val name: String,
        val icon: String,
        val category: String,
        val author: String,
        val packageName: String,
        /** `owner/repo`, lowercased. Empty when the app has no repository. */
        val repo: String,
        val version: String,
        val size: String,
        val platform: String,
        val downloadUrl: String,
        val releaseKeyword: String,
    )

    private val MAGIC = byteArrayOf(
        'O'.code.toByte(), 'R'.code.toByte(), 'D'.code.toByte(), '3'.code.toByte(),
    )

    // ------------------------------------------------------------------
    // Addressing — the client computes a path, it never fetches an index
    // ------------------------------------------------------------------

    fun fnv1a64(key: String): Long {
        var h = -3750763034362895579L          // 0xCBF29CE484222325 as a signed Long
        for (byte in key.toByteArray(Charsets.UTF_8)) {
            h = h xor (byte.toLong() and 0xFFL)
            h *= 0x100000001B3L                // wraps on overflow, as intended
        }
        return h
    }

    /**
     * Tier 0 / 1 partition name. Read `part_bytes` from index.json rather than
     * hardcoding 1: it becomes 2 once the catalogue passes ~10 million apps.
     */
    fun partOf(key: String, partBytes: Int = 1): String {
        var h = fnv1a64(key)
        val sb = StringBuilder(partBytes * 2)
        repeat(partBytes) {
            sb.append(HEX[(h and 0xFFL).toInt() ushr 4])
            sb.append(HEX[(h and 0xFFL).toInt() and 0x0F])
            h = h ushr 8
        }
        return sb.toString()
    }

    /** Tier 2 / 3 bucket pair: `detail/<h1>/<h2>.json`, `releases/<h1>/<h2>.json`. */
    fun shardOf(key: String): Pair<String, String> {
        val h = fnv1a64(key)
        return hex2(h and 0xFFL) to hex2((h ushr 8) and 0xFFL)
    }

    fun detailPath(key: String): String = shardOf(key).let { (a, b) -> "detail/$a/$b.json" }
    fun releasesPath(repo: String): String = shardOf(repo).let { (a, b) -> "releases/$a/$b.json" }
    fun catalogPath(id: String, partBytes: Int = 1): String = "catalog/${partOf(id, partBytes)}.bin"
    fun versionsPath(id: String, partBytes: Int = 1): String = "versions/${partOf(id, partBytes)}.bin"

    // ------------------------------------------------------------------
    // Tier 0 — catalog/<pp>.bin
    // ------------------------------------------------------------------

    fun decodeCatalog(bytes: ByteArray): List<CatalogApp> {
        checkMagic(bytes)
        val r = Reader(bytes)
        val schema = r.u8()
        require(schema == SCHEMA) { "unsupported data plane schema $schema" }
        val count = r.varint()
        val tables = ArrayList<List<String>>(COLUMNS.size)
        repeat(COLUMNS.size) { tables.add(r.table()) }
        val hosts = r.table()

        val out = ArrayList<CatalogApp>(count)
        repeat(count) {
            val v = arrayOfNulls<String>(COLUMNS.size)
            for (i in COLUMNS.indices) {
                val idx = r.varint()
                v[i] = when (i) {
                    2 -> {                                  // icon: host index + remainder
                        val rest = r.str()
                        val host = hosts.getOrNull(idx).orEmpty()
                        if (host.isEmpty()) rest else host + rest
                    }
                    9 -> PLATFORMS.getOrNull(idx) ?: "Unknown"
                    else -> tables[i].getOrNull(idx).orEmpty()
                }
            }
            out.add(
                CatalogApp(
                    id = v[0].orEmpty(), name = v[1].orEmpty(), icon = v[2].orEmpty(),
                    category = v[3].orEmpty(), author = v[4].orEmpty(),
                    packageName = v[5].orEmpty(), repo = v[6].orEmpty(),
                    version = v[7].orEmpty(), size = v[8].orEmpty(),
                    platform = v[9].orEmpty(), downloadUrl = v[10].orEmpty(),
                    releaseKeyword = v[11].orEmpty(),
                ),
            )
        }
        return out
    }

    // ------------------------------------------------------------------
    // Tier 1 — versions/<pp>.bin
    // ------------------------------------------------------------------

    /** id -> latest version. The whole update-check answer. */
    fun decodeVersions(bytes: ByteArray): Map<String, String> {
        checkMagic(bytes)
        val r = Reader(bytes)
        val schema = r.u8()
        require(schema == SCHEMA) { "unsupported data plane schema $schema" }
        val count = r.varint()
        val ids = r.table()
        val versions = r.table()
        val out = HashMap<String, String>(count * 2)
        repeat(count) {
            val idIdx = r.varint()
            val verIdx = r.varint()
            out[ids.getOrNull(idIdx).orEmpty()] = versions.getOrNull(verIdx).orEmpty()
        }
        return out
    }

    // ------------------------------------------------------------------
    // Tier 3 — asset URL reconstruction
    // ------------------------------------------------------------------

    /**
     * `base =`
     *   releaseB == ""              -> repoPrefix + tag + "/"
     *   releaseB starts with http   -> releaseB
     *   otherwise                   -> repoPrefix + releaseB
     *
     * The encoder only omits an asset URL when this rule reproduces it exactly,
     * so a non-empty `assetUrl` always wins. Never rebuild a URL from an asset
     * name yourself: hosts URL-encode names inconsistently (`+` becomes `%2B`).
     */
    fun releaseBase(repoPrefix: String, tag: String, releaseB: String): String = when {
        releaseB.isEmpty() -> if (repoPrefix.isEmpty()) "" else "$repoPrefix$tag/"
        releaseB.startsWith("http") -> releaseB
        else -> repoPrefix + releaseB
    }

    fun assetUrl(base: String, assetName: String, storedUrl: String): String =
        if (storedUrl.isNotEmpty()) storedUrl else base + assetName

    // ------------------------------------------------------------------

    private val HEX = "0123456789abcdef".toCharArray()

    private fun hex2(value: Long): String {
        val v = (value and 0xFFL).toInt()
        return "${HEX[v ushr 4]}${HEX[v and 0x0F]}"
    }

    private fun checkMagic(b: ByteArray) {
        require(b.size >= 5) { "data plane file too short" }
        for (i in MAGIC.indices) {
            require(b[i] == MAGIC[i]) { "bad magic: not an Orion data plane file" }
        }
    }

    private class Reader(private val b: ByteArray) {
        var p = 4                     // callers have already validated the magic

        fun u8(): Int {
            require(p < b.size) { "truncated data plane file" }
            return b[p++].toInt() and 0xFF
        }

        fun varint(): Int {
            var shift = 0
            var result = 0
            while (true) {
                val byte = u8()
                result = result or ((byte and 0x7F) shl shift)
                if (byte and 0x80 == 0) return result
                shift += 7
                require(shift <= 35) { "varint too long" }
            }
        }

        fun str(): String {
            val n = varint()
            require(n >= 0 && p + n <= b.size) { "truncated string" }
            val s = String(b, p, n, Charsets.UTF_8)
            p += n
            return s
        }

        fun table(): List<String> {
            val n = varint()
            val list = ArrayList<String>(n)
            repeat(n) { list.add(str()) }
            return list
        }
    }
}
