// forza_acl — C ABI ACL 2.1 sample decompress for the Forza Blender addon.
#include <acl/core/compressed_tracks.h>
#include <acl/core/error_result.h>
#include <acl/core/sample_rounding_policy.h>
#include <acl/core/track_writer.h>
#include <acl/decompression/decompress.h>

#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#if defined(_WIN32)
#  define FORZA_ACL_API extern "C" __declspec(dllexport)
#else
#  define FORZA_ACL_API extern "C" __attribute__((visibility("default")))
#endif

// Stable integer error codes (C ABI; never throw across the boundary).
enum forza_acl_status : int
{
	FORZA_ACL_OK = 0,
	FORZA_ACL_ERR_INVALID_BUFFER = -1,
	FORZA_ACL_ERR_WRONG_TRACK_TYPE = -2,
	FORZA_ACL_ERR_NULL_OUTPUT = -3,
	FORZA_ACL_ERR_OUTPUT_TOO_SMALL = -4,
	FORZA_ACL_ERR_BAD_SAMPLE_INDEX = -5,
	FORZA_ACL_ERR_INIT_CONTEXT = -6,
	FORZA_ACL_ERR_OVERFLOW = -7,
	FORZA_ACL_ERR_BAD_DIMENSIONS = -8,
};

struct qvv_writer final : public acl::track_writer
{
	rtm::qvvf* output = nullptr;
	explicit qvv_writer(rtm::qvvf* out) : output(out) {}

	RTM_FORCE_INLINE void RTM_SIMD_CALL write_rotation(uint32_t track_index, rtm::quatf_arg0 rotation)
	{
		output[track_index].rotation = rotation;
	}
	RTM_FORCE_INLINE void RTM_SIMD_CALL write_translation(uint32_t track_index, rtm::vector4f_arg0 translation)
	{
		output[track_index].translation = translation;
	}
	RTM_FORCE_INLINE void RTM_SIMD_CALL write_scale(uint32_t track_index, rtm::vector4f_arg0 scale)
	{
		output[track_index].scale = scale;
	}
};

static const acl::compressed_tracks* as_tracks(const void* data, int size, acl::error_result* err_out)
{
	if (!data || size < 32)
	{
		if (err_out)
			*err_out = acl::error_result("buffer too small");
		return nullptr;
	}
	const auto* tracks = reinterpret_cast<const acl::compressed_tracks*>(data);
	if (static_cast<uint32_t>(size) < tracks->get_size())
	{
		if (err_out)
			*err_out = acl::error_result("buffer size < compressed size");
		return nullptr;
	}
	const acl::error_result valid = tracks->is_valid(true);
	if (valid.any())
	{
		if (err_out)
			*err_out = valid;
		return nullptr;
	}
	return tracks;
}

static void store_pose(const rtm::qvvf* pose, uint32_t num_tracks, float* out_qvv)
{
	for (uint32_t ti = 0; ti < num_tracks; ++ti)
	{
		float* dst = out_qvv + ti * 12;
		rtm::quat_store(pose[ti].rotation, dst + 0);
		rtm::vector_store(pose[ti].translation, dst + 4);
		rtm::vector_store(pose[ti].scale, dst + 8);
	}
}

// Match the single-sample helper: sample time = index / sample_rate when
// num_samples > 1, else 0. Rounding policy remains nearest.
static float sample_time_for_index(uint32_t sample_index, uint32_t num_samples, float sample_rate)
{
	return (num_samples > 1) ? (static_cast<float>(sample_index) / sample_rate) : 0.0f;
}

FORZA_ACL_API int forza_acl_info(
	const void* compressed_data,
	int compressed_size,
	int* out_num_tracks,
	int* out_num_samples,
	float* out_sample_rate,
	float* out_duration,
	int* out_version)
{
	try
	{
		acl::error_result err;
		const acl::compressed_tracks* tracks = as_tracks(compressed_data, compressed_size, &err);
		if (!tracks)
			return FORZA_ACL_ERR_INVALID_BUFFER;
		if (tracks->get_track_type() != acl::track_type8::qvvf)
			return FORZA_ACL_ERR_WRONG_TRACK_TYPE;
		if (out_num_tracks)
			*out_num_tracks = static_cast<int>(tracks->get_num_tracks());
		if (out_num_samples)
			*out_num_samples = static_cast<int>(tracks->get_num_samples_per_track());
		if (out_sample_rate)
			*out_sample_rate = tracks->get_sample_rate();
		if (out_duration)
			*out_duration = tracks->get_duration();
		if (out_version)
			*out_version = static_cast<int>(tracks->get_version());
		return FORZA_ACL_OK;
	}
	catch (...)
	{
		return FORZA_ACL_ERR_INVALID_BUFFER;
	}
}

// Decompress one sample into out_qvv (num_tracks * 12 floats):
// [qx,qy,qz,qw, tx,ty,tz,tw, sx,sy,sz,sw] per track.
// sample_index < 0 selects the last sample.
// Returns 0 on success, negative on error.
FORZA_ACL_API int forza_acl_decompress_sample(
	const void* compressed_data,
	int compressed_size,
	int sample_index,
	float* output,
	int output_float_capacity,
	int* out_num_tracks)
{
	try
	{
		if (!output)
			return FORZA_ACL_ERR_NULL_OUTPUT;

		acl::error_result err;
		const acl::compressed_tracks* tracks = as_tracks(compressed_data, compressed_size, &err);
		if (!tracks)
			return FORZA_ACL_ERR_INVALID_BUFFER;
		if (tracks->get_track_type() != acl::track_type8::qvvf)
			return FORZA_ACL_ERR_WRONG_TRACK_TYPE;

		const uint32_t num_tracks = tracks->get_num_tracks();
		const uint32_t num_samples = tracks->get_num_samples_per_track();
		const float sample_rate = tracks->get_sample_rate();
		if (out_num_tracks)
			*out_num_tracks = static_cast<int>(num_tracks);

		if (num_tracks == 0 || num_samples == 0)
			return FORZA_ACL_ERR_BAD_DIMENSIONS;

		const int needed = static_cast<int>(num_tracks) * 12;
		if (output_float_capacity < needed)
			return FORZA_ACL_ERR_OUTPUT_TOO_SMALL;

		uint32_t si = 0;
		if (sample_index < 0)
			si = num_samples - 1;
		else if (static_cast<uint32_t>(sample_index) >= num_samples)
			return FORZA_ACL_ERR_BAD_SAMPLE_INDEX;
		else
			si = static_cast<uint32_t>(sample_index);

		acl::decompression_context<acl::default_transform_decompression_settings> context;
		if (!context.initialize(*tracks))
			return FORZA_ACL_ERR_INIT_CONTEXT;

		std::vector<rtm::qvvf> pose(num_tracks);
		qvv_writer writer(pose.data());
		const float t = sample_time_for_index(si, num_samples, sample_rate);
		context.seek(t, acl::sample_rounding_policy::nearest);
		context.decompress_tracks(writer);
		store_pose(pose.data(), num_tracks, output);
		return FORZA_ACL_OK;
	}
	catch (...)
	{
		return FORZA_ACL_ERR_INVALID_BUFFER;
	}
}

// Decompress every sample into a flat float buffer:
// output[(sample_index * num_tracks + track_index) * 12 + component]
// Uses the same index→time and nearest rounding as forza_acl_decompress_sample.
FORZA_ACL_API int forza_acl_decompress_all(
	const void* compressed_data,
	int compressed_size,
	float* output,
	int output_float_capacity,
	int* out_num_tracks,
	int* out_num_samples)
{
	try
	{
		if (!output || !out_num_tracks || !out_num_samples)
			return FORZA_ACL_ERR_NULL_OUTPUT;
		if (compressed_size <= 0 || output_float_capacity <= 0)
			return FORZA_ACL_ERR_INVALID_BUFFER;

		acl::error_result err;
		const acl::compressed_tracks* tracks = as_tracks(compressed_data, compressed_size, &err);
		if (!tracks)
			return FORZA_ACL_ERR_INVALID_BUFFER;
		if (tracks->get_track_type() != acl::track_type8::qvvf)
			return FORZA_ACL_ERR_WRONG_TRACK_TYPE;

		const uint32_t num_tracks = tracks->get_num_tracks();
		const uint32_t num_samples = tracks->get_num_samples_per_track();
		const float sample_rate = tracks->get_sample_rate();

		if (num_tracks == 0 || num_samples == 0)
			return FORZA_ACL_ERR_BAD_DIMENSIONS;

		// Guard overflow: samples * tracks * 12 floats.
		const uint64_t floats_needed_u64 =
			static_cast<uint64_t>(num_samples) * static_cast<uint64_t>(num_tracks) * 12ull;
		if (floats_needed_u64 > static_cast<uint64_t>(std::numeric_limits<int>::max()))
			return FORZA_ACL_ERR_OVERFLOW;
		const int floats_needed = static_cast<int>(floats_needed_u64);
		if (output_float_capacity < floats_needed)
			return FORZA_ACL_ERR_OUTPUT_TOO_SMALL;

		acl::decompression_context<acl::default_transform_decompression_settings> context;
		if (!context.initialize(*tracks))
			return FORZA_ACL_ERR_INIT_CONTEXT;

		std::vector<rtm::qvvf> pose(num_tracks);
		qvv_writer writer(pose.data());
		for (uint32_t si = 0; si < num_samples; ++si)
		{
			const float t = sample_time_for_index(si, num_samples, sample_rate);
			context.seek(t, acl::sample_rounding_policy::nearest);
			context.decompress_tracks(writer);
			store_pose(pose.data(), num_tracks, output + static_cast<size_t>(si) * num_tracks * 12u);
		}
		*out_num_tracks = static_cast<int>(num_tracks);
		*out_num_samples = static_cast<int>(num_samples);
		return FORZA_ACL_OK;
	}
	catch (...)
	{
		return FORZA_ACL_ERR_INVALID_BUFFER;
	}
}
